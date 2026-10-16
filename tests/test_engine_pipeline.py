"""Port v1 PipelineTests/JobStateTests to frozen jobs, executions and real SQLite.

Git merge/delete/process cases stay in v1 until sessions 1.7/1.8. Validation/role
cases were ported in session 1.2. See docs/pipeline-port.md for the case mapping.
"""

import copy
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_factory import __version__
from agent_factory.engine import db, pipeline, reports
from agent_factory.engine.config import RepoConfig, template_config


def execution(identifier, uses, provider="codex", model="gpt-6.1-sol", effort="medium"):
    return {
        "id": identifier,
        "uses": uses,
        "provider": provider,
        "profiles": {
            provider: {
                "model": model,
                "effort": effort
            }
        }
    }


class Engine:

    def __init__(self, root):
        self.root = root
        self.connection = pipeline.open_repo(root)
        self.head = "head-1"
        self.job_id = "001-pipe"
        roles = root / "factory" / "roles"
        roles.mkdir(parents=True)
        for name, kind in (("writer", "task"), ("quality", "review"), ("risk", "review"),
                           ("gate", "approval")):
            (roles / f"{name}.md").write_text(f"---\ntype: {kind}\n---\n{name} instructions.\n",
                                              encoding="utf-8")
        self.raw = template_config()
        self.raw.update(merge="verify", maxRounds=2)
        self.raw["pipelines"]["full"] = [
            execution("draft", "writer"),
            {
                "id":
                "checks",
                "parallel": [
                    execution("quality-check", "quality"),
                    execution("risk-check", "risk", "claude", "opus", "high")
                ],
                "onChanges":
                "draft"
            },
            execution("signoff", "gate"),
        ]
        self.create()

    def config(self):
        return RepoConfig.model_validate(self.raw, context={"root": self.root})

    def create(self, job_id="001-pipe", **kwargs):
        return pipeline.create_job(self.connection,
                                   job_id,
                                   "Do a thing",
                                   config=self.config(),
                                   worktree=str(self.root / job_id),
                                   branch=f"factory/{job_id}",
                                   base_head="head-0",
                                   preamble="Stable instructions.\n",
                                   preamble_version="test-v1",
                                   **kwargs)

    def load(self):
        return pipeline.load(self.connection, self.job_id)

    def start(self):
        return pipeline.advance_job(self.connection, self.job_id, current_head=self.head)

    def plan(self):
        return pipeline.step_plan(*self.load())

    def results(self, overrides=None, only=None):
        tasks = [t for t in self.plan() if only is None or t["exec_id"] in only]
        results = {}
        for task in tasks:
            with db.tx(self.connection):
                self.connection.execute("UPDATE executions SET state='running' WHERE id=?",
                                        (task["id"], ))
            first = {
                "status": "STATUS: READY",
                "spec": "# Spec",
                "verdict": "VERDICT: PASS",
                "decision": "APPROVE"
            }[task["role"]["contract"]]
            first = (overrides or {}).get(task["exec_id"], first)
            relative = (f"executions/{task['step_id']}.{task['exec_id']}/"
                        f"r{task['round']}/a1/report.md")
            path = self.root / "factory" / "jobs" / self.job_id / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(first + "\n\nEvidence.\n", encoding="utf-8")
            os.utime(path, ns=(1001_000_000_000, 1001_000_000_000))
            job, _ = self.load()
            step = next(s for s in job["frozen"]["pipeline"] if s["id"] == task["step_id"])
            report = reports.read_result(path,
                                         task["role"]["contract"],
                                         t_launch=1000.0,
                                         on_changes=bool(step["onChanges"]))
            results[task["id"]] = pipeline.Completion(report.verdict, self.head, relative)
        return results

    def finish(self, overrides=None, only=None):
        results = self.results(overrides, only)
        return pipeline.advance_job(self.connection,
                                    self.job_id,
                                    current_head=self.head,
                                    results=results)


@pytest.fixture
def engine(tmp_path):
    engine = Engine(tmp_path)
    yield engine
    engine.connection.close()


def test_new_writes_full_job_and_history(engine):
    assert (engine.root / "factory/state/factory.db").is_file()
    job, rows = engine.load()
    assert job["state"] == "queued" and rows == []
    assert (job["seq"], job["round"], job["returns_used"]) == (1, 1, 0)
    assert job["branch"] == "factory/001-pipe" and job["base_head"] == "head-0"
    frozen = job["frozen"]
    assert frozen["engine_version"] == __version__
    assert frozen["preamble"] == "Stable instructions.\n" and frozen["preamble_version"] == "test-v1"
    assert frozen["merge"] == "verify" and frozen["permissions"] == "auto"
    assert frozen["sensitivePaths"] == engine.raw["sensitivePaths"]
    assert frozen["concurrency"] == {"perJob": 4}
    assert frozen["timeouts"] == {"executionMinutes": 60}
    assert [r[0] for r in engine.connection.execute("SELECT type FROM events")] == ["job.state"]


def test_new_refuses_existing_job_or_sequence_without_partial_history(engine):
    before = list(engine.connection.execute("SELECT * FROM events"))
    for job_id in (engine.job_id, "001-other"):
        with pytest.raises(sqlite3.IntegrityError):
            engine.create(job_id)
    assert list(engine.connection.execute("SELECT * FROM events")) == before
    # v2 stores claims in start_queue; no pre-created job folder is required (§6.2).
    engine.create("002-next")


@pytest.mark.parametrize("job_id", ["", "../x", "a/b", ".hidden", "1-../escape", "1-Upper"])
def test_bad_job_ids_are_refused(engine, job_id):
    with pytest.raises(pipeline.PipelineError, match="job id"):
        engine.create(job_id)


def test_custom_pipeline_finishes_with_verify_and_retains_reports(engine):
    engine.start()
    assert engine.load()[0]["current_step"] == "draft"
    assert engine.finish().updates["current_step"] == "checks"
    assert engine.finish().updates["current_step"] == "signoff"
    assert engine.finish().action == "needs_human"
    job, rows = engine.load()
    assert (job["state"], job["pause_reason"], job["approved_head"]) == ("needs_human", "verify",
                                                                         engine.head)
    assert len(rows) == 4 and all(r["state"] == "done" for r in rows)
    for row in rows:
        assert (engine.root / "factory" / "jobs" / engine.job_id / row["report_path"]).is_file()
    pipeline.check_human_approval(job, rows, current_head=engine.head)
    assert engine.plan() == ()
    assert engine.start().action == "wait"


def test_auto_approval_is_ready_for_merge(engine):
    engine.raw["merge"] = "auto"
    engine.create("002-auto")
    engine.job_id = "002-auto"
    engine.start()
    engine.finish()
    engine.finish()
    assert engine.finish().action == "approved"
    job, _ = engine.load()
    assert (job["state"], job["approved_head"], job["pause_reason"]) == ("approved", engine.head,
                                                                         None)


def test_retry_is_bounded_and_invalidates_downstream_results(engine):
    engine.start()
    engine.finish()
    engine.finish({"quality-check": "VERDICT: CHANGES"})
    job, rows = engine.load()
    assert (job["current_step"], job["round"], job["returns_used"]) == ("draft", 2, 1)
    assert len([r for r in rows if r["state"] == "done"]) == 3
    assert [t["exec_id"] for t in engine.plan()] == ["draft"]
    engine.finish()
    assert {t["round"] for t in engine.plan()} == {2}
    engine.finish({"quality-check": "VERDICT: CHANGES"})
    job, rows = engine.load()
    assert (job["state"], job["pause_reason"]) == ("needs_human", "changes")
    assert len(rows) == 6
    pipeline.rework_job(engine.connection,
                        engine.job_id,
                        "Fix the custom review",
                        current_head=engine.head)
    job, rows = engine.load()
    assert (job["current_step"], job["returns_used"], job["round"]) == ("draft", 0, 3)
    assert (engine.root /
            "factory/jobs/001-pipe/rework-1.md").read_text() == "Fix the custom review\n"
    engine.finish()
    engine.finish()
    engine.finish()
    assert engine.load()[0]["pause_reason"] == "verify"


@pytest.mark.parametrize("target,budget", [(False, 2), (True, 1)])
def test_changes_without_target_or_budget_needs_human(engine, target, budget):
    engine.raw["maxRounds"] = budget
    if not target:
        del engine.raw["pipelines"]["full"][1]["onChanges"]
    engine.create("002-changes")
    engine.job_id = "002-changes"
    engine.start()
    engine.finish()
    engine.finish({"quality-check": "VERDICT: CHANGES"})
    assert engine.load()[0]["pause_reason"] == "changes"


def test_resume_only_dispatches_missing_reviewers(engine):
    engine.start()
    engine.finish()
    engine.finish(only={"quality-check"})
    assert [task["exec_id"] for task in engine.plan()] == ["risk-check"]
    job, rows = engine.load()
    assert next(e for e in rows if e["exec_id"] == "quality-check")["result"] == "PASS"
    assert job["current_step"] == "checks"
    engine.finish()
    assert engine.load()[0]["current_step"] == "signoff"


def test_parallel_reviewers_use_their_own_provider_settings(engine):
    engine.start()
    engine.finish()
    tasks = engine.plan()
    assert [(t["exec_id"], t["uses"], t["provider"], t["model"], t["effort"])
            for t in tasks] == [("quality-check", "quality", "codex", "gpt-6.1-sol", "medium"),
                                ("risk-check", "risk", "claude", "opus", "high")]


def test_same_role_in_group_has_distinct_execution_paths(engine):
    engine.raw["pipelines"]["full"][1]["parallel"][1]["uses"] = "quality"
    engine.create("002-repeat")
    engine.job_id = "002-repeat"
    engine.start()
    engine.finish()
    engine.finish()
    _, rows = engine.load()
    reviews = [row for row in rows if row["type"] == "review"]
    assert len({row["report_path"] for row in reviews}) == 2
    assert all(row["uses"] == "quality" for row in reviews)


def test_reusing_role_in_later_steps_keeps_separate_settings(engine):
    engine.raw["pipelines"]["full"].insert(
        1, execution("polish", "writer", model="gpt-6-luna", effort="low"))
    engine.create("002-reuse")
    engine.job_id = "002-reuse"
    engine.start()
    assert engine.plan()[0]["model"] == "gpt-6.1-sol"
    engine.finish()
    task = engine.plan()[0]
    assert (task["exec_id"], task["model"], task["effort"]) == ("polish", "gpt-6-luna", "low")


def test_pipeline_models_limits_roles_and_preamble_are_frozen(engine):
    original, _ = engine.load()
    engine.raw.update(merge="auto", maxRounds=5, permissions="bypass")
    draft = engine.raw["pipelines"]["full"][0]
    draft.update(timeoutMinutes=5, maxBudgetUsd=None)
    draft["profiles"]["codex"]["model"] = "gpt-6-luna"
    engine.raw["pipelines"]["full"].pop(1)
    path = engine.root / "factory/roles/writer.md"
    path.write_text("---\ntype: task\n---\nNew instructions.\n", encoding="utf-8")
    engine.create("002-new")
    new, _ = pipeline.load(engine.connection, "002-new")
    path.unlink()
    engine.start()
    task = engine.plan()[0]
    assert (task["model"], task["timeoutMinutes"]) == ("gpt-6.1-sol", 60)
    assert "writer instructions" in task["role"]["instructions"]
    assert task["role"]["sha256"] == original["frozen"]["pipeline"][0]["executions"][0]["role"][
        "sha256"]
    assert new["frozen"]["pipeline"][0]["executions"][0]["role"]["sha256"] != task["role"]["sha256"]
    engine.finish()
    assert engine.load()[0]["current_step"] == "checks"
    assert engine.load()[0]["frozen"] == original["frozen"]
    # Returned plans are independent values, not mutable aliases into frozen data.
    task["role"]["instructions"] = "changed"
    assert engine.load()[0]["frozen"] == original["frozen"]


def test_blocked_task_cannot_be_accepted_by_human(engine):
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    job, rows = engine.load()
    assert (job["state"], job["pause_reason"], job["approved_head"]) == ("needs_human", "blocked",
                                                                         None)
    with pytest.raises(pipeline.PipelineError, match="completed decision"):
        pipeline.check_human_approval(job, rows, current_head=engine.head)


def test_configured_steps_cannot_be_skipped(engine):
    engine.start()
    with db.tx(engine.connection):
        engine.connection.execute("UPDATE jobs SET current_step='signoff' WHERE id=?",
                                  (engine.job_id, ))
    with pytest.raises(pipeline.PipelineError, match="prerequisite step draft"):
        engine.plan()


def test_missing_provider_settings_prevents_new_job(engine):
    del engine.raw["pipelines"]["full"][0]["profiles"]["codex"]
    with pytest.raises(ValidationError, match="profiles"):
        engine.create("002-missing")
    with pytest.raises(pipeline.PipelineError, match="unknown job"):
        pipeline.load(engine.connection, "002-missing")


def test_selected_provider_uses_its_own_frozen_role(engine):
    path = engine.root / "factory/roles/writer.codex.md"
    path.write_text("---\ntype: task\n---\nCodex instructions.\n", encoding="utf-8")
    engine.create("002-codex")
    path.unlink()
    engine.job_id = "002-codex"
    engine.start()
    assert engine.plan()[0]["role"]["source"] == str(path)
    assert engine.plan()[0]["role"]["instructions"] == "Codex instructions.\n".replace(
        "\n", os.linesep)


def test_commit_after_approval_cannot_be_accepted(engine):
    engine.start()
    for _ in range(3):
        engine.finish()
    with pytest.raises(pipeline.PipelineError, match="changed after approval"):
        pipeline.check_human_approval(*engine.load(), current_head="later-head")


def test_escalation_needs_explicit_human_acceptance(engine):
    engine.start()
    engine.finish()
    engine.finish()
    engine.finish({"signoff": "ESCALATE"})
    job, rows = engine.load()
    assert (job["state"], job["pause_reason"], job["approved_head"]) == ("needs_human", "escalate",
                                                                         engine.head)
    pipeline.check_human_approval(job, rows, current_head=engine.head)
    assert engine.start().action == "wait"


@pytest.mark.parametrize("step", ["review", "approval"])
def test_new_commit_invalidates_review_and_approval(engine, step):
    engine.start()
    engine.finish()
    if step == "approval":
        engine.finish()
    engine.head = "changed-during-checks"
    results = engine.results()
    before = engine.load()
    with pytest.raises(pipeline.PipelineError, match="branch changed"):
        pipeline.advance_job(engine.connection,
                             engine.job_id,
                             current_head=engine.head,
                             results=results)
    assert engine.load() == before


def test_default_full_runs_all_builtin_steps(engine):
    engine.raw = template_config()
    engine.create("002-default")
    engine.job_id = "002-default"
    engine.start()
    assert engine.plan()[0]["role"]["contract"] == "spec"
    for expected in ("build", "review", "approve"):
        engine.finish()
        assert engine.load()[0]["current_step"] == expected
    engine.finish()
    assert engine.load()[0]["pause_reason"] == "verify"


def test_task_after_review_cannot_silently_invalidate_evidence(engine):
    engine.raw["pipelines"]["full"].insert(2, execution("polish", "writer"))
    engine.create("002-late-task")
    engine.job_id = "002-late-task"
    engine.start()
    engine.finish()
    engine.finish()
    engine.head = "polished-head"
    engine.finish()
    results = engine.results()
    with pytest.raises(pipeline.PipelineError, match="changed after checks"):
        pipeline.advance_job(engine.connection,
                             engine.job_id,
                             current_head=engine.head,
                             results=results)


def test_wait_is_idempotent_and_pure_decisions_do_not_mutate_inputs(engine):
    job, rows = engine.load()
    before = copy.deepcopy((job, rows))
    assert pipeline.advance(job, rows, current_head=engine.head).action == "advance"
    assert (job, rows) == before
    engine.start()
    initial = engine.load()
    events = engine.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    for _ in range(3):
        assert engine.start().action == "wait"
        assert len(engine.plan()) == 1
    assert engine.load() == initial
    assert engine.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events


def test_state_changes_append_history_with_round_and_clear_note(engine):
    engine.start()
    with db.tx(engine.connection):
        engine.connection.execute("UPDATE jobs SET note='old note' WHERE id=?", (engine.job_id, ))
    engine.finish()
    engine.finish({"quality-check": "VERDICT: CHANGES"})
    history = [
        json.loads(row[0]) for row in engine.connection.execute(
            "SELECT payload FROM events WHERE type='job.state' ORDER BY id")
    ]
    assert [h.get("current_step") for h in history] == [None, "draft", "checks", "draft"]
    assert [h["round"] for h in history] == [1, 1, 1, 2]
    assert engine.load()[0]["note"] is None


def test_invalid_results_change_nothing(engine):
    engine.start()
    results = engine.results()
    identifier = next(iter(results))
    before = engine.load()
    for bad in ("MAYBE", "PASS", "APPROVE"):
        with pytest.raises(pipeline.PipelineError, match="invalid result"):
            pipeline.advance_job(
                engine.connection,
                engine.job_id,
                current_head=engine.head,
                results={identifier: pipeline.Completion(bad, engine.head, "report.md")})
        assert engine.load() == before
    with pytest.raises(pipeline.PipelineError, match="current step and round"):
        pipeline.advance_job(engine.connection,
                             engine.job_id,
                             current_head=engine.head,
                             results={999: next(iter(results.values()))})
    assert engine.load() == before


def test_result_record_and_next_step_insertion_are_one_transaction(engine):
    engine.start()
    results = engine.results()
    before = engine.load()
    events = list(engine.connection.execute("SELECT * FROM events"))
    with db.tx(engine.connection):
        engine.connection.execute(
            "CREATE TRIGGER reject_next BEFORE INSERT ON executions "
            "WHEN NEW.step_id='checks' BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        pipeline.advance_job(engine.connection,
                             engine.job_id,
                             current_head=engine.head,
                             results=results)
    assert engine.load() == before
    assert list(engine.connection.execute("SELECT * FROM events")) == events
    assert not engine.connection.in_transaction


def test_rework_prefers_builder_preserves_upstream_and_resets_budget(engine):
    engine.raw = template_config()
    engine.create("002-builtin")
    engine.job_id = "002-builtin"
    engine.start()
    for _ in range(4):
        engine.finish()
    pipeline.rework_job(engine.connection, engine.job_id, "Improve it", current_head=engine.head)
    job, rows = engine.load()
    assert (job["state"], job["current_step"], job["round"], job["returns_used"],
            job["approved_head"]) == ("running", "build", 2, 0, None)
    assert next(e for e in rows if e["step_id"] == "spec")["state"] == "done"
    engine.finish()
    assert {t["round"] for t in engine.plan()} == {2}
    engine.finish()
    engine.finish()
    pipeline.check_human_approval(*engine.load(), current_head=engine.head)


def test_rework_falls_back_to_unfinished_prerequisite(engine):
    engine.raw = template_config()
    engine.create("002-blocked-spec")
    engine.job_id = "002-blocked-spec"
    # A custom first task can BLOCK; spec's contract itself has only READY.
    job, rows = engine.load()
    job.update(state="needs_human", pause_reason="blocked", current_step="spec")
    transition = pipeline.rework(job, rows, "Clarify request", current_head=engine.head)
    assert transition.updates["current_step"] == "spec"


def test_rework_supersedes_only_nonterminal_downstream_executions(engine):
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    job, rows = engine.load()
    extra = [{
        "id": i + 10,
        "step_id": "checks",
        "round": 1,
        "state": state
    } for i, state in enumerate(("pending", "claimed", "running", "failed", "stopped",
                                 "superseded"))]
    transition = pipeline.rework(job, [*rows, *extra], "Try again", current_head=engine.head)
    assert transition.supersede == (10, 11, 12)


def test_rework_requires_finished_job_note_and_task(engine):
    with pytest.raises(pipeline.PipelineError, match="stop or finish"):
        pipeline.rework_job(engine.connection, engine.job_id, "Again", current_head=engine.head)
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    with pytest.raises(pipeline.PipelineError, match="needs a note"):
        pipeline.rework_job(engine.connection, engine.job_id, " ", current_head=engine.head)
    job, rows = engine.load()
    job["frozen"]["pipeline"] = [job["frozen"]["pipeline"][-1]]
    with pytest.raises(pipeline.PipelineError, match="no task"):
        pipeline.rework(job, rows, "Again", current_head=engine.head)


def test_rework_notes_can_be_recovered_after_file_write_failure(engine, monkeypatch):
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    original = os.replace
    monkeypatch.setattr(pipeline.os, "replace", lambda *args:
                        (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        pipeline.rework_job(engine.connection,
                            engine.job_id,
                            "Durable request",
                            current_head=engine.head)
    assert engine.load()[0]["state"] == "running"
    monkeypatch.setattr(pipeline.os, "replace", original)
    pipeline.materialize_rework_notes(engine.connection, engine.job_id)
    path = engine.root / "factory/jobs/001-pipe/rework-1.md"
    assert path.read_text() == "Durable request\n"
    pipeline.materialize_rework_notes(engine.connection, engine.job_id)
    assert not list(path.parent.glob(".rework-*.tmp"))


def test_rework_notes_keep_numbering_after_events_are_pruned(engine):
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    pipeline.rework_job(engine.connection, engine.job_id, "First note", current_head=engine.head)
    with db.tx(engine.connection):
        engine.connection.execute("DELETE FROM events WHERE type='note'")
    engine.finish({"draft": "STATUS: BLOCKED"})
    pipeline.rework_job(engine.connection, engine.job_id, "Second note", current_head=engine.head)
    folder = engine.root / "factory/jobs/001-pipe"
    assert (folder / "rework-1.md").read_text() == "First note\n"
    assert (folder / "rework-2.md").read_text() == "Second note\n"


def test_concurrent_writers_do_not_duplicate_transitions_or_lose_history(engine):

    def start(_):
        connection = pipeline.open_repo(engine.root)
        try:
            return pipeline.advance_job(connection, engine.job_id, current_head=engine.head).action
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        actions = list(pool.map(start, range(30)))
    assert actions.count("advance") == 1 and actions.count("wait") == 29
    assert len(engine.load()[1]) == 1
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM events WHERE type='job.state'").fetchone()[0] == 2


def test_paused_job_preserves_completed_reviewers_and_same_executions_on_resume(engine):
    engine.start()
    engine.finish()
    engine.finish(only={"quality-check"})
    before = engine.load()[1]
    with db.tx(engine.connection):
        engine.connection.execute("UPDATE jobs SET state='paused', pause_reason='auth' WHERE id=?",
                                  (engine.job_id, ))
    assert engine.plan() == () and engine.start().action == "wait"
    with db.tx(engine.connection):
        engine.connection.execute("UPDATE jobs SET state='running', pause_reason=NULL WHERE id=?",
                                  (engine.job_id, ))
    assert [t["exec_id"] for t in engine.plan()] == ["risk-check"]
    assert engine.load()[1] == before


def test_approval_changes_without_target_is_rejected_even_for_direct_results(engine):
    engine.start()
    engine.finish()
    engine.finish()
    results = engine.results()
    identifier = next(iter(results))
    with pytest.raises(pipeline.PipelineError, match="requires onChanges"):
        pipeline.advance_job(
            engine.connection,
            engine.job_id,
            current_head=engine.head,
            results={identifier: pipeline.Completion("CHANGES", engine.head, "report.md")})


def test_unknown_pipeline_is_rejected_before_writes(engine):
    with pytest.raises(pipeline.PipelineError, match="unknown pipeline"):
        engine.create("002-unknown", pipeline="missing")
    assert engine.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_completion_and_rewind_decisions_leave_input_evidence_unchanged(engine):
    engine.start()
    engine.finish()
    results = engine.results({"quality-check": "VERDICT: CHANGES"})
    job, rows = engine.load()
    before = copy.deepcopy((job, rows, results))
    transition = pipeline.advance(job, rows, results, current_head=engine.head)
    assert transition.action == "rewind"
    assert len(transition.completed) == 2
    assert not transition.supersede
    assert (job, rows, results) == before


def test_rework_failure_rolls_back_note_and_rewind_together(engine):
    engine.start()
    engine.finish({"draft": "STATUS: BLOCKED"})
    before = engine.load()
    with db.tx(engine.connection):
        engine.connection.execute(
            "CREATE TRIGGER reject_rework BEFORE INSERT ON events "
            "WHEN NEW.type='note' BEGIN SELECT RAISE(ABORT, 'note rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="note rejected"):
        pipeline.rework_job(engine.connection, engine.job_id, "Again", current_head=engine.head)
    assert engine.load() == before
    assert not list((engine.root / "factory/jobs/001-pipe").glob("rework-*.md"))


@pytest.mark.parametrize("changes", [0, 1, 2])
def test_fast_happy_path_changes_loop_and_budget(engine, changes):
    engine.raw["defaultPipeline"] = "fast"
    engine.raw["merge"] = "auto"
    engine.create("002-fast")
    engine.job_id = "002-fast"
    engine.start()
    engine.finish()
    for index in range(changes):
        engine.finish({"approve": "CHANGES"})
        job, _ = engine.load()
        if index == 1:
            assert (job["state"], job["pause_reason"]) == ("needs_human", "changes")
            return
        assert (job["current_step"], job["round"], job["returns_used"]) == ("build", 2, 1)
        engine.head = "rebuilt-head"
        engine.finish()
    engine.finish()
    job, rows = engine.load()
    assert job["state"] == "approved"
    assert job["approved_head"] == engine.head
    assert len(rows) == 2 * (changes + 1)
