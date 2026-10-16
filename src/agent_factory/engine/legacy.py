"""Read v1 evidence without importing its runtime or enrolling jobs in the scheduler."""

import json
from pathlib import Path
import sqlite3
from typing import Any

from agent_factory import run_cli
from agent_factory.engine import db, gitops, pipeline
from agent_factory.engine.config import load_config


def folder(root: Path, job_id: str) -> Path:
    gitops.worktree_path(root, job_id)  # Validate the same ids as the ported git operations.
    target = (root / "factory/jobs" / job_id).resolve()
    if target.parent != (root / "factory/jobs").resolve():
        raise ValueError("Legacy job folder escapes factory/jobs")
    return target


def load(root: Path, job_id: str) -> dict[str, Any]:
    path = folder(root, job_id) / "job.json"
    if not path.resolve().is_relative_to(path.parent):
        raise ValueError("Legacy job metadata escapes its folder")
    if not path.is_file():
        raise KeyError(f"Unknown legacy job: {job_id}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("id") != job_id:
        raise ValueError("Legacy job id does not match its folder")
    return value


def describe(root: Path, job_id: str) -> dict[str, Any]:
    value = load(root, job_id)
    directory = folder(root, job_id)
    reports = sorted(
        p.relative_to(directory).as_posix() for p in directory.rglob("*.md")
        if p.is_file() and p.resolve().is_relative_to(directory))
    return {
        "id": job_id,
        "title": value.get("feature", job_id),
        "stage": value.get("stage", "unknown"),
        "read_only": True,
        "reports": reports
    }


def jobs(root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted((root / "factory/jobs").glob("*/job.json")):
        result.append(describe(root, path.parent.name))
    return result


def action(root: Path, job_id: str, action: str, body: dict[str, Any]) -> str:
    if action not in {"approve", "merge", "delete"}:
        raise ValueError("Legacy jobs are read-only; only Merge and Delete are available")
    value = load(root, job_id)
    stage = value.get("stage")
    if action != "delete":
        if stage not in {"approve", "approved", "needs-human"}:
            raise ValueError("Only an approved or completed waiting legacy job can be merged")
        if "pipeline" in value and not value.get("pipelineComplete"):
            raise ValueError("Finish the configured pipeline before merging this legacy job")
    # Adapt only for the duration of a git operation. The durable v2 jobs/attempts
    # tables never contain legacy work, so recovery and scheduling cannot run it.
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        db.migrate(connection, Path(pipeline.__file__).parent / "migrations/repo")
        approved = value.get("approval", {}).get("head")
        if not approved and "pipeline" not in value and action != "delete":
            approved = gitops.checked("rev-parse", value["branch"], cwd=root)
        base = gitops.checked("rev-parse", "main", cwd=root)
        frozen = {"sensitivePaths": load_config(root).sensitivePaths}
        state = ("approved" if stage in {"approve", "approved", "needs-human"} else
                 "merged" if stage == "merged" else "running")
        connection.execute(
            "INSERT INTO jobs (id,seq,title,pipeline_name,frozen,state,branch,worktree,"
            "base_head,approved_head,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, 1, value.get("feature", job_id), "legacy", json.dumps(frozen), state,
             value.get("branch", f"factory/{job_id}"),
             value.get("worktree", str(gitops.worktree_path(root, job_id))), base, approved,
             value.get("created", ""), value.get("updated", "")))
        if action == "delete":
            return gitops.delete(root, connection, job_id, force=body.get("force", False))
        message = gitops.merge(root,
                               connection,
                               job_id,
                               human=True,
                               confirm_sensitive=body.get("confirm_sensitive", False))
        value["stage"] = "merged"
        value["updated"] = pipeline._now()
        run_cli.atomic_json(folder(root, job_id) / "job.json", value)
        return message
    finally:
        connection.close()
