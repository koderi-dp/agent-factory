"""Read-only view of what a job changed against its base branch.

Only read-only git plumbing runs here, always with --no-optional-locks, so reading
changes never modifies the job's worktree, index or branch.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_factory.engine import gitops

MAX_FILES = 2000
MAX_DIFF_BYTES = 512 * 1024

_KINDS = {"A": "added", "M": "modified", "D": "deleted", "T": "type_changed"}


class ChangesUnavailable(gitops.GitError):
    pass


class UnknownFile(LookupError):
    pass


def _git(root: Path, *args: str) -> str:
    return gitops.checked("--no-optional-locks", *args, cwd=root)


def _range(root: Path, job: Mapping[str, Any]) -> tuple[str, str]:
    branch = job["branch"]
    if branch != "factory/" + job["id"]:
        raise ChangesUnavailable("unexpected job branch")
    approved = job.get("approved_head")
    if gitops.branch_exists(root, branch):
        tip = _git(root, "rev-parse", "--verify", f"refs/heads/{branch}")
    elif approved and gitops.git(
            "--no-optional-locks", "cat-file", "-e", f"{approved}^{{commit}}",
            cwd=root).returncode == 0:
        tip = approved
    else:
        raise ChangesUnavailable("the job's branch no longer exists")
    base = job.get("base_head")
    if not base:
        try:
            base = _git(root, "merge-base", gitops.base_branch(job), tip)
        except gitops.GitError as error:
            raise ChangesUnavailable(f"cannot find the job's base commit: {error}") from error
    return base, tip


def _parse_status(text: str) -> list[dict[str, Any]]:
    parts = text.split("\0")
    entries = []
    index = 0
    while index < len(parts) and parts[index]:
        code = parts[index]
        if code[0] in "RC":
            old, new = parts[index + 1], parts[index + 2]
            index += 3
            entries.append({"path": new, "old_path": old, "kind": "renamed"})
        else:
            path = parts[index + 1]
            index += 2
            entries.append({
                "path": path,
                "old_path": None,
                "kind": _KINDS.get(code[0], "modified")
            })
    return entries


def _parse_numstat(text: str) -> dict[str, tuple[int | None, int | None]]:
    parts = text.split("\0")
    stats: dict[str, tuple[int | None, int | None]] = {}
    index = 0
    while index < len(parts) and parts[index]:
        added, deleted, path = parts[index].split("\t", 2)
        if path:
            index += 1
        else:
            path = parts[index + 2]
            index += 3
        binary = added == "-" and deleted == "-"
        stats[path] = (None, None) if binary else (int(added), int(deleted))
    return stats


def list_changes(root: str | Path, job: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(root)
    base, tip = _range(root, job)
    status = _git(root, "diff", "--name-status", "-z", "-M", "--no-ext-diff", base, tip)
    numstat = _git(root, "diff", "--numstat", "-z", "-M", "--no-ext-diff", base, tip)
    stats = _parse_numstat(numstat)
    files = []
    for entry in sorted(_parse_status(status), key=lambda item: item["path"]):
        additions, deletions = stats.get(entry["path"], (None, None))
        files.append({
            **entry, "additions": additions,
            "deletions": deletions,
            "binary": additions is None
        })
    return {
        "job_id": job["id"],
        "base": base,
        "head": tip,
        "files": files[:MAX_FILES],
        "truncated": len(files) > MAX_FILES
    }


def file_diff(root: str | Path, job: Mapping[str, Any], path: str) -> dict[str, Any]:
    root = Path(root)
    listing = list_changes(root, job)
    entry = next((item for item in listing["files"] if item["path"] == path), None)
    if entry is None:
        raise UnknownFile(path)
    text = ""
    truncated = False
    if not entry["binary"]:
        pathspecs = [f":(literal){path}"]
        if entry["old_path"]:
            pathspecs.append(f":(literal){entry['old_path']}")
        text = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "-M",
                    listing["base"], listing["head"], "--", *pathspecs)
        if len(text.encode("utf-8", errors="replace")) > MAX_DIFF_BYTES:
            cut = text.encode("utf-8", errors="replace")[:MAX_DIFF_BYTES].decode("utf-8",
                                                                                 errors="ignore")
            text = cut[:cut.rfind("\n") + 1] if "\n" in cut else cut
            truncated = True
    return {
        "path": entry["path"],
        "old_path": entry["old_path"],
        "kind": entry["kind"],
        "binary": entry["binary"],
        "diff": text,
        "truncated": truncated
    }
