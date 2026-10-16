"""Git worktrees and the v1 merge rules (plan §7.2, §11). Never push.

Callers serialise job mutations in the service. Git and process operations happen
outside SQLite transactions; only the resulting state/events use a short tx.
"""

from collections.abc import Mapping
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
from typing import Any

from agent_factory import run_cli
from agent_factory.engine import db, pipeline, ports, winjob


class GitError(ValueError):
    exit_code = 1


class Busy(GitError):
    exit_code = 3


class Sensitive(GitError):
    pass


def _matches(path: str, pattern: str) -> bool:
    """Case-sensitive repo globs: ** includes zero or more directories."""

    def match(parts: list[str], glob: list[str]) -> bool:
        if not glob:
            return not parts
        if glob[0] == "**":
            return match(parts, glob[1:]) or bool(parts and match(parts[1:], glob))
        return bool(parts and fnmatchcase(parts[0], glob[0]) and match(parts[1:], glob[1:]))

    return match(path.split("/"), pattern.split("/"))


def sensitive_paths(root: Path, job: Mapping[str, Any]) -> list[str]:
    """Check the frozen policy against both sides of renames, including deletions."""
    frozen = job["frozen"]
    paths = checked("diff",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    f"{job['base_head']}..{job['approved_head']}",
                    cwd=root).split("\0")
    return sorted(path for path in paths
                  if path and any(_matches(path, pattern) for pattern in frozen["sensitivePaths"]))


def git(*args: str, cwd: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args],
                          cwd=cwd,
                          capture_output=True,
                          text=True,
                          encoding="utf-8",
                          errors="replace")


def checked(*args: str, cwd: str | Path) -> str:
    result = git(*args, cwd=cwd)
    if result.returncode:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
    # Do not strip spaces: -z filename lists can begin with a literal space.
    return result.stdout.rstrip("\r\n")


def branch_exists(root: str | Path, branch: str) -> bool:
    return git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root).returncode == 0


def head(worktree: str | Path) -> str:
    return checked("rev-parse", "HEAD", cwd=worktree)


def branch_head(job: Mapping[str, Any], *, clean: bool = False) -> str:
    worktree = job["worktree"]
    if checked("branch", "--show-current", cwd=worktree) != job["branch"]:
        raise GitError("the job worktree must be on " + job["branch"])
    if clean and checked("status", "--porcelain", cwd=worktree):
        raise GitError("the job worktree has uncommitted changes; commit the step's changes first")
    return head(worktree)


def validate_result(job: Mapping[str, Any], execution: Mapping[str, Any], result: str) -> str:
    """Validate the git-dependent report contract and return the observed head."""
    current = branch_head(job, clean=execution["type"] != "task" or result == "READY")
    if execution["type"] != "task" and current != execution["head_before"]:
        raise GitError("the branch changed while this step was running")
    if execution["uses"] == "builder" and result == "READY":
        base = job["base_head"]
        if (not base
                or git("merge-base", "--is-ancestor", base, current, cwd=job["worktree"]).returncode
                or int(checked("rev-list", "--count", f"{base}..{current}",
                               cwd=job["worktree"])) < 1):
            raise GitError("the builder must add at least one commit since base_head")
    return current


def worktree_path(root: str | Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9]+-[a-z0-9][a-z0-9-]*", job_id):
        raise GitError(f"invalid job id: {job_id!r}")
    root = Path(root).resolve()
    return root.parent / (root.name + "-factory") / job_id


def create_worktree(root: str | Path, job_id: str) -> tuple[Path, str]:
    """Fresh-checkout setup remains the first task's responsibility (§11)."""
    path = worktree_path(root, job_id)
    checked("worktree", "add", "-b", f"factory/{job_id}", str(path), "main", cwd=root)
    return path, head(path)


def _worktree(root: Path, job: Mapping[str, Any]) -> Path:
    target = worktree_path(root, job["id"])
    expected = target.resolve()
    actual = Path(job["worktree"]).resolve()
    if actual != expected or expected.parent != target.parent.resolve():
        raise GitError(f"refusing to remove worktree outside the job's expected path: {actual}")
    if job["branch"] != "factory/" + job["id"]:
        raise GitError("unexpected job branch")
    return actual


def remove_worktree(root: Path, job: Mapping[str, Any], *, force: bool = False) -> bool:
    path = _worktree(root, job)
    if not path.exists():
        return False
    # Six attempts: the initial try and up to five 1-second lock retries.
    for attempt in range(6 if os.name == "nt" else 1):
        result = git("worktree", "remove", *(("--force", ) if force else ()), str(path), cwd=root)
        if not result.returncode:
            return True
        if os.name == "nt" and attempt < 5:
            time.sleep(1)
    raise GitError("couldn't remove the worktree: " + result.stderr.strip())


def free_ports(connection: sqlite3.Connection, root: Path, job_id: str) -> None:
    """Stop only verified attempt trees, never a process found by port number.

    Also write Stop for an in-flight wrapper still publishing proc.json. Keep
    evidence and refuse deletion if a live attempt cannot yet be identified.
    """
    rows = connection.execute(
        "SELECT a.* FROM attempts a JOIN executions e ON e.id=a.execution_id WHERE e.job_id=?",
        (job_id, )).fetchall()
    for row in rows:
        directory = root / "factory/jobs" / job_id / row["dir"]
        members = winjob.owned_tree(directory, row["owner_token"])
        live = row["state"] in {"launching", "running"}
        if live or members:
            directory.mkdir(parents=True, exist_ok=True)
            run_cli.atomic_write(directory / "stop", b"")
        if live and not members and not (directory / "exit.json").exists():
            # A claim without a command cannot have launched. For a prepared
            # launch, let the service finish its ownership/recovery protocol.
            if (directory / "command.json").exists():
                raise GitError("attempt is still launching; finish Stop before deleting the job")
        if members:
            winjob.signal_tree(directory, row["owner_token"], members, kill=False)
            deadline = time.monotonic() + 10  # §9's graceful Stop interval
            while any(winjob.alive(member) for member in members) and time.monotonic() < deadline:
                time.sleep(0.05)
            winjob.signal_tree(directory, row["owner_token"], members, kill=True)
            deadline = time.monotonic() + 5
            while any(winjob.alive(member) for member in members) and time.monotonic() < deadline:
                time.sleep(0.05)
            if any(winjob.alive(member) for member in members):
                raise GitError("attempt tree did not stop; preserving the job")


def _state(connection: sqlite3.Connection,
           job_id: str,
           state: str,
           reason: str | None = None,
           note: str | None = None) -> None:
    with db.tx(connection):
        now = pipeline._now()
        connection.execute("UPDATE jobs SET state=?,pause_reason=?,note=?,updated_at=? WHERE id=?",
                           (state, reason, note, now, job_id))
        pipeline._event(connection, job_id, now, "job.state", {
            "state": state,
            "pause_reason": reason,
            "note": note
        })


def merge(root: str | Path,
          connection: sqlite3.Connection,
          job_id: str,
          *,
          human: bool = False,
          confirm_sensitive: bool = False,
          machine: sqlite3.Connection | None = None,
          repo_id: str | None = None) -> str:
    root = Path(root).resolve()
    job, executions = pipeline.load(connection, job_id)
    branch = job["branch"]
    _worktree(root, job)
    current = branch_head(job, clean=True)
    if job["state"] == "needs_human" and human:
        pipeline.check_human_approval(job, executions, current_head=current)
    elif job["state"] != "approved":
        raise GitError("only an approved or completed waiting job can be merged")
    if not job["approved_head"] or current != job["approved_head"]:
        raise GitError("the branch changed after approval; rework and review it again")
    if not branch_exists(root, branch):
        raise GitError(f"branch {branch} doesn't exist")
    matching = sensitive_paths(root, job)
    if matching and not (human and confirm_sensitive):
        detail = "Sensitive paths: " + ", ".join(matching) + "; approve with --confirm-sensitive"
        _state(connection, job_id, "needs_human", "sensitive", detail)
        raise Sensitive(detail)
    try:
        checkout = checked("branch", "--show-current", cwd=root)
        if checkout != "main":
            raise Busy(f"the main checkout is on {checkout or 'a detached HEAD'}, not main")
        if git("diff", "--cached", "--quiet", cwd=root).returncode:
            raise Busy("the main checkout has staged changes; commit or unstage them first")
        ours = set(checked("diff", "--name-only", "-z", cwd=root).split("\0")) - {""}
        theirs = set(
            checked("diff", "--name-only", "-z", f"main...{current}", cwd=root).split("\0")) - {""}
        clash = sorted(ours & theirs)
        if clash:
            raise Busy("you have uncommitted changes in files this job also changes: " +
                       ", ".join(clash[:5]))
    except Busy as error:
        _state(connection, job_id, "needs_human", "busy_merge", str(error))
        raise
    # Merge the checked immutable hash: a ref moving during merge cannot sneak
    # unreviewed commits in. A later cleanup also refuses to delete that ref.
    result = git("merge", "--no-ff", current, "-m", f"Merge {branch}", cwd=root)
    if result.returncode:
        if git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=root).returncode == 0:
            checked("merge", "--abort", cwd=root)
        detail = "git merge failed and was undone: " + (result.stdout +
                                                        result.stderr).strip()[-400:]
        _state(connection, job_id, "needs_human", "changes", detail)
        raise GitError(detail)
    notes = []
    try:
        free_ports(connection, root, job_id)
        if checked("rev-parse", branch, cwd=root) != current:
            raise GitError("branch changed after merge; worktree and branch retained")
        remove_worktree(root, job)
        checked("branch", "-d", branch, cwd=root)
    except GitError as error:
        notes.append(str(error))
    _state(connection, job_id, "merged", note="; ".join(notes) or None)
    if machine is not None and repo_id is not None:
        ports.release(machine, repo_id, job_id)
        with db.tx(connection):
            connection.execute("UPDATE jobs SET port_base=NULL WHERE id=?", (job_id, ))
    return f"merged {branch} into main" + (f" ({'; '.join(notes)})" if notes else "")


def delete(root: str | Path,
           connection: sqlite3.Connection,
           job_id: str,
           *,
           force: bool = False,
           machine: sqlite3.Connection | None = None,
           repo_id: str | None = None) -> str:
    root = Path(root).resolve()
    job, _ = pipeline.load(connection, job_id)
    _worktree(root, job)  # Check all absolute targets before any removal (§11).
    folder = (root / "factory/jobs" / job_id).resolve()
    if folder.parent != (root / "factory/jobs").resolve():
        raise GitError("job folder escapes factory/jobs")
    if job["state"] in {"queued", "running", "paused"} and not force:
        raise GitError(f"{job_id} is still running; stop it first, or pass --force")
    free_ports(connection, root, job_id)
    removed = []
    if remove_worktree(root, job, force=True):
        removed.append("worktree")
    if branch_exists(root, job["branch"]):
        checked("branch", "-D", job["branch"], cwd=root)
        removed.append("branch")
    if folder.exists():
        shutil.rmtree(folder)
    removed.append("job folder")
    with db.tx(connection):
        connection.execute("DELETE FROM start_queue WHERE job_id=?", (job_id, ))
        # events intentionally has no FK (§6.2); delete job-scoped history
        # explicitly alongside the execution/attempt FK cascades.
        connection.execute("DELETE FROM events WHERE job_id=?", (job_id, ))
        connection.execute("DELETE FROM jobs WHERE id=?", (job_id, ))
        pipeline._event(connection, None, pipeline._now(), "job.state", {"deleted": job_id})
    if machine is not None and repo_id is not None:
        ports.release(machine, repo_id, job_id)
    return f"deleted {job_id}: " + ", ".join(removed)
