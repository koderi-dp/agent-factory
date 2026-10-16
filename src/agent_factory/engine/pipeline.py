"""Frozen pipelines and deterministic transitions, applied in one short DB tx (§10)."""

import copy
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_factory import __version__
from agent_factory.engine import db
from agent_factory.engine.config import RepoConfig
from agent_factory.engine.crash import crash_at

Row = Mapping[str, Any]
TERMINAL = {"done", "failed", "stopped", "superseded"}
RESULTS = {
    "task": {"READY", "BLOCKED"},
    "review": {"PASS", "CHANGES"},
    "approval": {"APPROVE", "ESCALATE", "CHANGES"}
}


class PipelineError(ValueError):
    pass


@dataclass(frozen=True)
class Completion:
    """Evidence already validated by reports and git checks (§7.2)."""

    result: str
    head_after: str
    report_path: str


@dataclass(frozen=True)
class Transition:
    action: str
    updates: dict[str, Any] = field(default_factory=dict)
    pending: tuple[dict[str, Any], ...] = ()
    supersede: tuple[int, ...] = ()
    completed: tuple[dict[str, Any], ...] = ()
    rework_note: str | None = None


def freeze(config: RepoConfig,
           pipeline: str | None = None,
           *,
           preamble: str,
           preamble_version: str) -> dict[str, Any]:
    """Snapshot resolved roles and all execution policy; never re-read role files."""
    name = pipeline or config.defaultPipeline
    if name not in config.pipelines:
        raise PipelineError(f"unknown pipeline: {name}")
    frozen = config.model_dump(exclude={"pipelines", "defaultPipeline"}, exclude_none=True)
    frozen.update(pipeline_name=name,
                  engine_version=__version__,
                  preamble=preamble,
                  preamble_version=preamble_version)
    steps = []
    for step in config.pipelines[name]:
        entries = []
        for execution in step.executions:
            role = config.role_for(name, execution.id)
            entry = execution.model_dump(exclude_none=True)
            entry["role"] = {
                key: getattr(role, key)
                for key in ("name", "provider", "type", "contract", "description", "text",
                            "instructions", "source", "sha256")
            }
            entries.append(entry)
        steps.append({
            "id": step.id,
            "label": step.label or step.id,
            "type": entries[0]["role"]["type"],
            "onChanges": step.onChanges,
            "executions": entries
        })
    frozen["pipeline"] = steps
    return frozen


def _frozen(job: Row) -> dict[str, Any]:
    value = job["frozen"]
    return json.loads(value) if isinstance(value, str) else value


def _rows(executions: Sequence[Row], step_id: str, round_: int) -> list[Row]:
    return [
        e for e in executions
        if e["step_id"] == step_id and e["round"] == round_ and e["state"] != "superseded"
    ]


def _evidence(steps: list[dict[str, Any]], executions: Sequence[Row]) -> dict[str, list[Row]]:
    """A newer upstream round invalidates all older downstream evidence (§6.1)."""
    floor = 1
    evidence = {}
    for step in steps:
        rounds = [e["round"] for e in executions if e["step_id"] == step["id"]]
        floor = max([floor, *rounds])
        evidence[step["id"]] = _rows(executions, step["id"], floor)
    return evidence


def _complete(step: Row, rows: Sequence[Row]) -> bool:
    return (len(rows) == len(step["executions"])
            and {e["exec_id"]
                 for e in rows} == {e["id"]
                                    for e in step["executions"]}
            and all(e["state"] == "done" and e["result"] in RESULTS[step["type"]] for e in rows))


def _prerequisites(job: Row, executions: Sequence[Row], index: int) -> None:
    steps = _frozen(job)["pipeline"]
    evidence = _evidence(steps, executions)
    for prior in steps[:index]:
        rows = evidence[prior["id"]]
        if not _complete(prior, rows) or any(e["result"] not in {"READY", "PASS"} for e in rows):
            raise PipelineError(f"finish prerequisite step {prior['id']}")


def _pending(step: Row, round_: int, head: str) -> tuple[dict[str, Any], ...]:
    rows = []
    for entry in step["executions"]:
        profile = entry["profiles"][entry["provider"]]
        rows.append({
            "step_id": step["id"],
            "exec_id": entry["id"],
            "round": round_,
            "type": entry["role"]["type"],
            "uses": entry["uses"],
            "provider": entry["provider"],
            "model": profile["model"],
            "effort": profile["effort"],
            "state": "pending",
            "head_before": head
        })
    return tuple(rows)


def step_plan(job: Row, executions: Sequence[Row]) -> tuple[dict[str, Any], ...]:
    """Return only pending executions, with their frozen role/profile/limits."""
    if job["state"] != "running":
        return ()
    frozen = _frozen(job)
    steps = frozen["pipeline"]
    index = next((i for i, step in enumerate(steps) if step["id"] == job["current_step"]), None)
    if index is None:
        raise PipelineError(f"unknown current step: {job['current_step']}")
    _prerequisites(job, executions, index)
    step = steps[index]
    entries = {e["id"]: e for e in step["executions"]}
    rows = _rows(executions, step["id"], job["round"])
    return tuple({
        **dict(row), "role":
        copy.deepcopy(entries[row["exec_id"]]["role"]),
        "profiles":
        copy.deepcopy(entries[row["exec_id"]]["profiles"]),
        "timeoutMinutes":
        entries[row["exec_id"]].get("timeoutMinutes", frozen["timeouts"]["executionMinutes"]),
        "maxBudgetUsd":
        entries[row["exec_id"]].get("maxBudgetUsd")
    } for row in rows if row["state"] == "pending")


def rewind(job: Row,
           executions: Sequence[Row],
           target: str,
           *,
           current_head: str,
           automatic: bool = True,
           note: str | None = None) -> Transition:
    """Create a new round, retaining completed evidence as immutable history."""
    frozen = _frozen(job)
    steps = frozen["pipeline"]
    index = next((i for i, step in enumerate(steps) if step["id"] == target), None)
    if index is None or steps[index]["type"] != "task":
        raise PipelineError(f"rewind target must be a task step: {target}")
    _prerequisites(job, executions, index)
    downstream = {step["id"] for step in steps[index:]}
    supersede = tuple(e["id"] for e in executions
                      if e["step_id"] in downstream and e["state"] not in TERMINAL)
    round_ = job["round"] + 1
    return Transition("rewind", {
        "state": "running",
        "current_step": target,
        "round": round_,
        "pause_reason": None,
        "note": None,
        "approved_head": None,
        "returns_used": job["returns_used"] + 1 if automatic else 0
    },
                      _pending(steps[index], round_, current_head),
                      supersede,
                      rework_note=note)


def rework(job: Row, executions: Sequence[Row], note: str, *, current_head: str) -> Transition:
    if job["state"] not in {"approved", "needs_human"}:
        raise PipelineError("stop or finish the current job before reworking it")
    if not note.strip():
        raise PipelineError("rework needs a note")
    steps = _frozen(job)["pipeline"]
    target = next((s for s in steps if any(e["uses"] == "builder" for e in s["executions"])), None)
    target = target or next((s for s in steps if s["type"] == "task"), None)
    if target is None:
        raise PipelineError("this pipeline has no task step to rework")
    # Preserve v1's fallback when an earlier task never finished successfully.
    evidence = _evidence(steps, executions)
    for prior in steps[:steps.index(target)]:
        rows = evidence[prior["id"]]
        if not _complete(prior, rows) or any(e["result"] not in {"READY", "PASS"} for e in rows):
            target = prior
            break
    return rewind(job,
                  executions,
                  target["id"],
                  current_head=current_head,
                  automatic=False,
                  note=note)


def advance(job: Row,
            executions: Sequence[Row],
            results: Mapping[int, Completion] | None = None,
            *,
            current_head: str) -> Transition:
    """Pure aggregation: accept validated results, wait, advance, rewind or decide."""
    if job["state"] not in {"queued", "running"}:
        if results:
            raise PipelineError("results require a running job")
        return Transition("wait")
    frozen = _frozen(job)
    steps = frozen["pipeline"]
    if job["state"] == "queued":
        if results:
            raise PipelineError("a queued job cannot have results")
        return Transition("advance", {
            "state": "running",
            "current_step": steps[0]["id"],
            "pause_reason": None,
            "note": None
        }, _pending(steps[0], job["round"], current_head))
    index = next((i for i, step in enumerate(steps) if step["id"] == job["current_step"]), None)
    if index is None:
        raise PipelineError(f"unknown current step: {job['current_step']}")
    _prerequisites(job, executions, index)
    step = steps[index]
    rows = [dict(e) for e in _rows(executions, step["id"], job["round"])]
    supplied = results or {}
    if set(supplied) - {e["id"] for e in rows}:
        raise PipelineError("results must belong to the current step and round")
    completed = []
    for row in rows:
        if row["id"] in supplied:
            result = supplied[row["id"]]
            if row["state"] != "running":
                raise PipelineError("only a running execution can accept a result")
            row.update(state="done",
                       result=result.result,
                       head_after=result.head_after,
                       report_path=result.report_path)
            completed.append(row)
        if row["state"] == "done":
            if row["result"] not in RESULTS[row["type"]]:
                raise PipelineError(f"invalid result for {row['type']}: {row['result']}")
            if row["type"] == "approval" and row["result"] == "CHANGES" and not step["onChanges"]:
                raise PipelineError("approval CHANGES requires onChanges")
            if row["type"] != "task" and (not row["head_before"]
                                          or row["head_after"] != row["head_before"]):
                raise PipelineError("the branch changed while this step was running")
    if not _complete(step, rows):
        return Transition("wait", completed=tuple(completed))
    if step["type"] != "task" and any(row["head_after"] != current_head for row in rows):
        raise PipelineError("the branch changed while this step was running")
    values = {row["result"] for row in rows}
    reason = None
    if "BLOCKED" in values:
        reason = "blocked"
    elif "CHANGES" in values:
        if step["onChanges"] and job["returns_used"] < frozen["maxRounds"] - 1:
            transition = rewind(job, executions, step["onChanges"], current_head=current_head)
            # Newly completed rows must not also be superseded by this transition.
            return Transition(transition.action, transition.updates, transition.pending,
                              tuple(i for i in transition.supersede if i not in supplied),
                              tuple(completed))
        reason = "changes"
    elif step["type"] == "approval":
        evidence = _evidence(steps, executions)
        for reviewed in steps[:index]:
            if reviewed["type"] == "review" and any(e["head_after"] != current_head
                                                    for e in evidence[reviewed["id"]]):
                raise PipelineError(f"the branch changed after {reviewed['id']}; rework required")
        if "ESCALATE" in values:
            reason = "escalate"
        elif frozen["merge"] == "verify":
            reason = "verify"
        else:
            # The service calls gitops.merge outside this tx; its shared guard
            # checks sensitive paths for both automatic and human merges (§10.3).
            return Transition("approved", {
                "state": "approved",
                "pause_reason": None,
                "note": None,
                "approved_head": current_head
            },
                              completed=tuple(completed))
    if reason:
        updates = {
            "state": "needs_human",
            "pause_reason": reason,
            "note": f"{step['id']}: {reason}"
        }
        if reason in {"verify", "escalate"}:
            updates["approved_head"] = current_head
        return Transition("needs_human", updates, completed=tuple(completed))
    next_step = steps[index + 1]
    return Transition("advance", {
        "current_step": next_step["id"],
        "note": None,
        "pause_reason": None
    },
                      _pending(next_step, job["round"], current_head),
                      completed=tuple(completed))


def check_human_approval(job: Row, executions: Sequence[Row], *, current_head: str) -> None:
    """Validate the non-git part of §10.4; merge and sensitive checks come later."""
    if (job["state"] != "needs_human"
            or job["pause_reason"] not in {"verify", "escalate", "sensitive", "busy_merge"}):
        raise PipelineError("only a completed decision can be accepted by a human")
    steps = _frozen(job)["pipeline"]
    _prerequisites(job, executions, len(steps) - 1)
    rows = _evidence(steps, executions)[steps[-1]["id"]]
    if not _complete(steps[-1], rows) or any(e["result"] not in {"APPROVE", "ESCALATE"}
                                             for e in rows):
        raise PipelineError("every step must finish before human approval")
    if not job["approved_head"] or current_head != job["approved_head"]:
        raise PipelineError("the branch changed after approval")


def open_repo(path: str | Path) -> sqlite3.Connection:
    connection = db.connect(Path(path) / "factory" / "state" / "factory.db")
    try:
        db.migrate(connection, Path(__file__).parent / "migrations" / "repo")
    except BaseException:
        connection.close()
        raise
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(connection: sqlite3.Connection, job_id: str, ts: str, type_: str, payload: Row) -> None:
    connection.execute("INSERT INTO events (job_id, ts, type, payload) VALUES (?, ?, ?, ?)",
                       (job_id, ts, type_, json.dumps(dict(payload))))


def create_job(connection: sqlite3.Connection,
               job_id: str,
               title: str,
               *,
               config: RepoConfig,
               worktree: str,
               branch: str,
               base_head: str,
               preamble: str,
               preamble_version: str,
               pipeline: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]+-[a-z0-9][a-z0-9-]*", job_id):
        raise PipelineError(f"invalid job id: {job_id!r}")
    snapshot = freeze(config, pipeline, preamble=preamble, preamble_version=preamble_version)
    now = _now()
    with db.tx(connection):
        connection.execute(
            "INSERT INTO jobs (id, seq, title, pipeline_name, frozen, state, branch, worktree, "
            "base_head, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
            (job_id, int(job_id.split("-", 1)[0]), title, snapshot["pipeline_name"],
             json.dumps(snapshot), branch, worktree, base_head, now, now))
        _event(connection, job_id, now, "job.state", {"state": "queued", "round": 1})
        job, _ = load(connection, job_id)
    return job


def load(connection: sqlite3.Connection,
         job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id, )).fetchone()
    if row is None:
        raise PipelineError(f"unknown job: {job_id}")
    job = dict(row)
    job["frozen"] = json.loads(job["frozen"])
    rows = connection.execute("SELECT * FROM executions WHERE job_id=? ORDER BY id", (job_id, ))
    return job, [dict(e) for e in rows]


def _apply(connection: sqlite3.Connection,
           job_id: str,
           transition: Transition,
           now: str,
           *,
           rework_floor: int = 0) -> None:
    for row in transition.completed:
        connection.execute(
            "UPDATE executions SET state='done', result=?, head_after=?, report_path=? WHERE id=?",
            (row["result"], row["head_after"], row["report_path"], row["id"]))
        _event(connection, job_id, now, "execution.state", row)
    for identifier in transition.supersede:
        connection.execute("UPDATE executions SET state='superseded' WHERE id=?", (identifier, ))
        _event(connection, job_id, now, "execution.state", {
            "id": identifier,
            "state": "superseded"
        })
    for row in transition.pending:
        columns = ["job_id", *row.keys(), "created_at"]
        cursor = connection.execute(
            f"INSERT INTO executions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            (job_id, *row.values(), now))
        _event(connection, job_id, now, "execution.state", {"id": cursor.lastrowid, **row})
    if transition.updates:
        updates = {**transition.updates, "updated_at": now}
        connection.execute(f"UPDATE jobs SET {', '.join(key + '=?' for key in updates)} WHERE id=?",
                           (*updates.values(), job_id))
        job, _ = load(connection, job_id)
        _event(
            connection, job_id, now, "job.state", {
                key: job[key]
                for key in ("state", "current_step", "round", "returns_used", "pause_reason",
                            "note", "approved_head")
            })
    if transition.rework_note is not None:
        # Durable source for rework-<k>.md, materialised outside the tx.
        previous = [
            json.loads(row[0]).get("number", 0) for row in connection.execute(
                "SELECT payload FROM events WHERE job_id=? AND type='note'", (job_id, ))
        ]
        number = max([rework_floor, *previous]) + 1
        _event(connection, job_id, now, "note", {
            "kind": "rework",
            "number": number,
            "text": transition.rework_note
        })


def advance_job(connection: sqlite3.Connection,
                job_id: str,
                *,
                current_head: str,
                results: Mapping[int, Completion] | None = None) -> Transition:
    """Read, decide, record results/history and enqueue the next step atomically."""
    # Git inspection/report IO must happen before entry; no subprocess or await in tx.
    with db.tx(connection):
        job, executions = load(connection, job_id)
        transition = advance(job, executions, results, current_head=current_head)
        _apply(connection, job_id, transition, _now())
        crash_at("before_advance_commit")
    return transition


def rework_job(connection: sqlite3.Connection, job_id: str, note: str, *,
               current_head: str) -> Transition:
    # Inspect files before entering tx. Existing files preserve numbering after event pruning.
    folder = _job_folder(connection, job_id)
    numbers = [
        int(path.stem.split("-", 1)[1]) for path in folder.glob("rework-*.md")
        if re.fullmatch(r"rework-[0-9]+", path.stem)
    ]
    with db.tx(connection):
        job, executions = load(connection, job_id)
        if connection.execute(
                "SELECT 1 FROM attempts a JOIN executions e ON e.id=a.execution_id "
                "WHERE e.job_id=? AND a.state IN ('launching','running')", (job_id, )).fetchone():
            raise PipelineError("wait for live attempts to exit before reworking")
        transition = rework(job, executions, note, current_head=current_head)
        _apply(connection, job_id, transition, _now(), rework_floor=max(numbers, default=0))
    materialize_rework_notes(connection, job_id)
    return transition


def _job_folder(connection: sqlite3.Connection, job_id: str) -> Path:
    load(connection, job_id)
    database_path = connection.execute("PRAGMA database_list").fetchone()[2]
    return Path(database_path).parent.parent / "jobs" / job_id


def materialize_rework_notes(connection: sqlite3.Connection, job_id: str) -> None:
    """Replay durable notes after commit; a crash cannot lose a human's request."""
    folder = _job_folder(connection, job_id)
    notes = [
        json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM events WHERE job_id=? AND type='note' ORDER BY id", (job_id, ))
    ]
    notes = [note for note in notes if note.get("kind") == "rework"]
    for note in notes:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rework-{note['number']}.md"
        content = (note["text"] + "\n").encode("utf-8")
        if path.is_file() and path.read_bytes() == content:
            continue
        descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=".rework-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    # TODO (plan §6.4, session 1.5): replay pending notes during startup recovery,
    # before event pruning, so a crash between commit and materialisation is repaired.
