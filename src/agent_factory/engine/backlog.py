"""Markdown backlog blocks, preserving the v1 format and indented details."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ITEM = re.compile(r"^[-*]\s+\[([ xX])\]\s+(\S.*)")
STARTED = re.compile(r"^[-*]\s+\[[xX]\]\s+(\S.*?)\s+\((\d+-[a-z0-9][a-z0-9-]*)\)\s*$")

PRIORITIES = ("high", "normal", "low")
STATUSES = ("ready", "in_progress", "needs_input", "completed")
DEFAULT_PRIORITY = "normal"
DEFAULT_STATUS = "ready"


def blocks(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# Backlog", ""]
    result = []
    i = 0
    while i < len(lines):
        match = ITEM.match(lines[i])
        block = [lines[i]]
        i += 1
        if match:
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith("  ")):
                block.append(lines[i])
                i += 1
        tail = []
        while len(block) > 1 and not block[-1].strip():
            tail.insert(0, block.pop())
        result.append({"open": bool(match and match[1] == " "), "lines": block})
        result.extend({"open": False, "lines": [line]} for line in tail)
    return result


def key(block: dict[str, Any]) -> str:
    return hashlib.sha256("\n".join(block["lines"]).encode("utf-8")).hexdigest()


def meta_path(path: Path) -> Path:
    return path.with_name("backlog.meta.json")


def _load_meta_document(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def read_meta(path: Path) -> dict[str, dict[str, str]]:
    result = {}
    for item_key, entry in (_load_meta_document(path) or {}).items():
        if not isinstance(entry, dict):
            continue
        values = {}
        if entry.get("priority") in PRIORITIES:
            values["priority"] = entry["priority"]
        if entry.get("status") in STATUSES:
            values["status"] = entry["status"]
        if values:
            result[item_key] = values
    return result


def _open_keys(path: Path) -> set[str]:
    return {key(block) for block in blocks(path) if block["open"]}


def items(path: Path) -> list[dict[str, Any]]:
    meta = read_meta(path)
    result = []
    for index, block in enumerate(block for block in blocks(path) if block["open"]):
        entry = meta.get(key(block), {})
        result.append({
            "index":
            index,
            "key":
            key(block),
            "title":
            ITEM.match(block["lines"][0])[2],
            "details":
            "\n".join(line[2:] if line.startswith("  ") else line for line in block["lines"][1:]),
            "priority":
            entry.get("priority", DEFAULT_PRIORITY),
            "status":
            entry.get("status", DEFAULT_STATUS)
        })
    return result


def _details(block: dict[str, Any]) -> str:
    return "\n".join(line[2:] if line.startswith("  ") else line for line in block["lines"][1:])


def started(path: Path) -> list[dict[str, Any]]:
    result = []
    for block in blocks(path):
        match = STARTED.match(block["lines"][0])
        if block["open"] or not match:
            continue
        result.append({
            "key": key(block),
            "title": match[1],
            "details": _details(block),
            "job": match[2]
        })
    return result


def validate_entry(title: Any, details: Any) -> str:
    if not isinstance(title, str) or not title.strip() or len(title) > 300 or any(c in title
                                                                                  for c in "\n\r"):
        raise ValueError("each item needs a one-line title of at most 300 characters")
    if not isinstance(details, str) or len(details) > 20000:
        raise ValueError("details must be text of at most 20,000 characters")
    return title.strip()


def block_lines(title: str, details: str, job: str | None = None) -> list[str]:
    head = f"- [x] {title} ({job})" if job else f"- [ ] {title}"
    return [head, *("  " + line if line else "" for line in details.splitlines())]


def rekey_meta(path: Path, old_key: str, new_key: str) -> bytes | None:
    document = _load_meta_document(path)
    if document is None:
        return None
    moved = dict(document)
    if old_key != new_key and old_key in moved:
        moved[new_key] = moved.pop(old_key)
    open_keys = _open_keys(path)
    moved = {k: v for k, v in moved.items() if k in open_keys}
    if moved == document:
        return None
    return _render_meta(moved)


def _render_meta(meta: dict[str, Any]) -> bytes:
    return (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8")


def update_meta(path: Path,
                item_key: str,
                *,
                priority: str | None = None,
                status: str | None = None) -> bytes:
    if priority is not None and priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    open_keys = _open_keys(path)
    if item_key not in open_keys:
        raise ValueError("Backlog item changed; refresh before trying again")
    meta = {k: v for k, v in read_meta(path).items() if k in open_keys}
    entry = {"priority": DEFAULT_PRIORITY, "status": DEFAULT_STATUS, **meta.get(item_key, {})}
    if priority is not None:
        entry["priority"] = priority
    if status is not None:
        entry["status"] = status
    meta[item_key] = entry
    defaults = {"priority": DEFAULT_PRIORITY, "status": DEFAULT_STATUS}
    full = {k: {**defaults, **v} for k, v in meta.items()}
    return _render_meta({k: v for k, v in full.items() if v != defaults})


def prune_meta(path: Path) -> bytes | None:
    document = _load_meta_document(path)
    if document is None:
        return None
    open_keys = _open_keys(path)
    kept = {k: v for k, v in document.items() if k in open_keys}
    if len(kept) == len(document):
        return None
    return _render_meta(kept)


def render(values: list[dict[str, Any]]) -> bytes:
    return ("\n".join(line for block in values for line in block["lines"]) + "\n").encode("utf-8")
