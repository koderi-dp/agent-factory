"""Execution packets: content, stable prefix and byte budgets (plan §8.2)."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_factory.engine import gitops, packet
from agent_factory.engine.adapters.codex import CodexAdapter
from test_engine_gitops import add_job, context_for, repo


def completed(context,
              identifier,
              *,
              role="builder",
              round_=1,
              result="READY",
              state="done",
              text=None):
    relative = f"executions/step.exec{identifier}/r{round_}/a1/report.md"
    source = context.root / "factory/jobs" / context.job["id"] / relative
    source.parent.mkdir(parents=True)
    source.write_text(
        text or
        "STATUS: READY\n## Decisions\nKeep it simple.\n## Checks run\n`pytest` -> pass\n## Acceptance\n1 -> test evidence\n",
        encoding="utf-8")
    return {
        "id": identifier,
        "exec_id": f"exec{identifier}",
        "step_id": "step",
        "uses": role,
        "round": round_,
        "state": state,
        "result": result,
        "report_path": relative
    }


def test_packet_sections_request_paths_ports_branch_and_handoffs(repo):
    job = add_job(repo)
    context = context_for(repo, {**job, "port_base": 5100})
    source = completed(context, 9)
    prompt = packet.build(context, executions=[source])
    data = prompt.packet
    assert list(data) == [key for key, _ in packet._SECTIONS]
    assert data["request"] == "Verbatim request\r\nwith details.\n"
    assert data["paths"]["ports"] == "5100, 5101, 5102"
    assert Path(data["paths"]["report_path"]).is_absolute()
    assert context.attempt["dir"] in data["paths"]["report_path"].replace("\\", "/")
    assert "job change" in data["branch_state"] and "+from the job" in data["branch_state"]
    assert "Keep it simple." in data["upstream_results"]
    assert "`pytest` -> pass" in data["upstream_results"]
    assert "exec9" in data["upstream_results"] and source["report_path"] in data[
        "upstream_results"].replace("\\", "/")
    assert "never push" in prompt.system.lower(
    ) and "never touch the main checkout" in prompt.system.lower()


def test_system_is_byte_identical_across_jobs_and_uses_frozen_role(repo):
    first = add_job(repo)
    second = add_job(repo, "002-other")
    a = packet.build(context_for(repo, first), executions=[])
    b = packet.build(context_for(repo, second), executions=[])
    assert a.system.encode("utf-8") == b.system.encode("utf-8")
    assert "\r" not in a.system
    assert first["id"] not in a.system and first["worktree"] not in a.system
    role = first["frozen"]["pipeline"][0]["executions"][0]["role"]
    assert role["instructions"].strip() in a.system
    role["instructions"] = "Frozen changed role.\r\n"
    assert "Frozen changed role." in packet.build(context_for(repo, first), executions=[]).system


def test_acceptance_full_and_fast_and_latest_rework(repo):
    context = context_for(repo, add_job(repo))
    folder = context.root / "factory/jobs" / context.job["id"]
    (folder / "rework-2.md").write_text("old")
    (folder / "rework-10.md").write_text("latest")
    spec = completed(context,
                     10,
                     role="spec-writer",
                     text="# Spec\n## Acceptance criteria\n1. Must work\n")
    builder = completed(context, 11)
    data = packet.build(context, executions=[spec, builder]).packet
    assert "1. Must work" in data["acceptance_criteria"]
    assert "latest" in data["request"] and "old" not in data["request"]
    approval = replace(context,
                       execution={
                           **context.execution, "step_id": "approve",
                           "exec_id": "approve",
                           "type": "approval"
                       })
    data = packet.build(approval, executions=[builder]).packet
    assert "test evidence" in data["acceptance_criteria"]
    assert data["paths"]["ports"] == "none"


def test_previous_round_findings_include_superseded_custom_reports(repo):
    context = context_for(repo, add_job(repo))
    context = replace(context, execution={**context.execution, "round": 2})
    previous = completed(context,
                         8,
                         role="reviewer-code",
                         result="CHANGES",
                         state="superseded",
                         text="VERDICT: CHANGES\n## Findings\n- [major] app.txt:2 — fix č\n")
    custom = completed(context,
                       9,
                       role="custom",
                       result="CHANGES",
                       state="superseded",
                       text="VERDICT: CHANGES\nDo every fix.")
    older = completed(context, 7, role="custom", result="CHANGES", round_=0, text="not this round")
    data = packet.build(context, executions=[previous, custom, older]).packet
    assert "app.txt:2 — fix č" in data["open_findings"] and "Do every fix." in data["open_findings"]
    assert "not this round" not in data["open_findings"]
    assert "exec8" in data["open_findings"] and "exec9" in data["open_findings"]
    assert not data["upstream_results"]


@pytest.mark.parametrize("kind,state,message", [("contract_retry", "invalid", "invalid first line"),
                                                ("fresh", "lost", "partial uncommitted work")])
def test_retry_context_and_resume_destination(repo, kind, state, message):
    context = context_for(repo, add_job(repo))
    context = replace(context,
                      attempt={
                          **context.attempt, "kind": kind
                      },
                      previous={
                          "state": state,
                          "error_detail": "invalid first line"
                      })
    assert message in packet.build(context, executions=[]).packet["retry_context"]
    context = replace(context,
                      attempt={
                          **context.attempt, "kind": "resume",
                          "session_id": "exact-session"
                      })
    prepared = CodexAdapter().prepare(context)
    assert prepared.prompt.startswith("Continue ") and len(prepared.prompt) < 1000
    assert str((context.directory / "report.md").resolve()) in prepared.prompt
    assert prepared.system not in prepared.prompt


def test_cap_trims_branch_before_handoffs_and_keeps_protected_sections(repo, monkeypatch):
    context = context_for(repo, add_job(repo))
    handoff = completed(context, 4, text="STATUS: READY\n## Decisions\n" + "upstream č" * 1000)
    monkeypatch.setattr(packet, "branch_state", lambda job: "branch č" * 20000)
    prompt = packet.build(context, executions=[handoff])
    assert len(prompt.text.encode("utf-8")) <= packet.MAX_PACKET_BYTES
    assert len(json.dumps(dict(prompt.packet), ensure_ascii=False,
                          indent=2).encode("utf-8")) + 1 <= packet.MAX_PACKET_BYTES
    assert "[omitted: branch state; see " in prompt.packet["branch_state"]
    assert "upstream č" * 1000 in prompt.packet["upstream_results"]
    assert "[omitted: upstream" not in prompt.packet["upstream_results"]
    assert prompt.packet["request"] == "Verbatim request\r\nwith details.\n"


def test_cap_trims_handoffs_second_and_never_truncates_findings_or_retry(repo, monkeypatch):
    context = context_for(repo, add_job(repo))
    context = replace(context,
                      execution={
                          **context.execution, "round": 2
                      },
                      attempt={
                          **context.attempt, "kind": "contract_retry"
                      },
                      previous={
                          "state": "invalid",
                          "error_detail": "FIX THIS"
                      })
    handoff = completed(context,
                        4,
                        round_=2,
                        text="STATUS: READY\n## Decisions\n" + "huge č" * 20000)
    finding = completed(context,
                        5,
                        role="reviewer-code",
                        result="CHANGES",
                        text="VERDICT: CHANGES\n## Findings\nEVERY FINDING")
    monkeypatch.setattr(packet, "branch_state", lambda job: "branch č" * 20000)
    prompt = packet.build(context, executions=[handoff, finding])
    assert len(prompt.text.encode("utf-8")) <= packet.MAX_PACKET_BYTES
    assert prompt.packet["branch_state"].startswith("[omitted:")
    assert "[omitted: upstream results; see " in prompt.packet["upstream_results"]
    assert "EVERY FINDING" in prompt.packet["open_findings"]
    assert "FIX THIS" in prompt.packet["retry_context"]


def test_oversized_protected_request_fails_explicitly(repo):
    context = context_for(repo, add_job(repo))
    (context.root / "factory/jobs" / context.job["id"] / "request.md").write_text(
        "č" * packet.MAX_PACKET_BYTES, encoding="utf-8")
    with pytest.raises(packet.PacketError, match="64 KiB"):
        packet.build(context, executions=[])


def test_large_diff_shares_hunks_and_marks_omitted_files(repo):
    job = add_job(repo)
    worktree = Path(job["worktree"])
    for name in ("large.txt", "other.txt"):
        (worktree / name).write_text("".join(f"line {n} č\n" for n in range(7000)),
                                     encoding="utf-8")
    gitops.checked("add", ".", cwd=worktree)
    gitops.checked("commit", "-qm", "large files", cwd=worktree)
    text = packet.branch_state(job)
    assert "diff --git a/large.txt" in text and "diff --git a/other.txt" in text
    assert text.count("[omitted: remaining diff hunks; see ") == 2
    assert "+line 0 č" in text


def test_custom_reports_without_sections_and_report_path_confinement(repo):
    context = context_for(repo, add_job(repo))
    row = completed(context, 3, role="custom", text="STATUS: READY\nNo sections")
    assert "[omitted: custom report has no handoff sections; see " in packet.build(
        context, executions=[row]).text
    row["report_path"] = "../../outside.md"
    with pytest.raises(packet.PacketError, match="escapes"):
        packet.build(context, executions=[row])


def test_review_packet_refuses_a_dirty_or_changed_head_before_launch(repo):
    context = context_for(repo, add_job(repo))
    context = replace(context,
                      execution={
                          **context.execution, "step_id": "approve",
                          "exec_id": "approve",
                          "type": "approval"
                      })
    worktree = Path(context.job["worktree"])
    (worktree / "partial.txt").write_text("work")
    with pytest.raises(gitops.GitError, match="uncommitted"):
        packet.build(context, executions=[])
    (worktree / "partial.txt").unlink()
    gitops.checked("commit", "-q", "--allow-empty", "-m", "changed before launch", cwd=worktree)
    with pytest.raises(packet.PacketError, match="changed since"):
        packet.build(context, executions=[])


def test_packet_selects_exact_contract_and_overrides_legacy_role_filenames(repo):
    context = context_for(repo, add_job(repo))
    role = context.job["frozen"]["pipeline"][0]["executions"][0]["role"]
    role["contract"] = "spec"
    context.job["frozen"]["preamble"] = packet.PREAMBLE
    prompt = packet.build(context, executions=[])
    assert prompt.packet["job"]["report_contract"] == (
        "Start the report with a Markdown '# ' heading, e.g. # Feature title")
    assert "do not create a second legacy report" in prompt.system
    assert "There is no job.json" in prompt.system
    assert "task STATUS: READY|BLOCKED (spec: # heading)" not in prompt.system
    role["contract"] = "status"
    prompt = packet.build(context, executions=[])
    assert prompt.packet["job"][
        "report_contract"] == "First line exactly STATUS: READY or STATUS: BLOCKED"


def test_parallel_review_artifacts_and_browser_sessions_are_isolated(repo):
    context = context_for(repo, add_job(repo))
    context.job["frozen"]["preamble"] = packet.PREAMBLE
    first = packet.build(context, executions=[])
    second_context = replace(context,
                             attempt={
                                 **context.attempt, "dir": "executions/review.ui/r1/a1"
                             })
    second = packet.build(second_context, executions=[])
    a, b = first.packet["paths"], second.packet["paths"]
    assert a["browser_session"] != b["browser_session"]
    assert a["browser_session"] == packet.build(context,
                                                executions=[]).packet["paths"]["browser_session"]
    assert a["artifacts_dir"] != b["artifacts_dir"]
    assert Path(a["artifacts_dir"]) == Path(a["report_path"]).parent
    assert not Path(a["artifacts_dir"]).is_relative_to(Path(context.job["worktree"]))
    assert "keep it clean throughout" in first.system
    assert "packet's browser_session" in first.system
