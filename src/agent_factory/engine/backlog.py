"""Markdown backlog blocks, preserving the v1 format and indented details."""

import hashlib
import re
from pathlib import Path
from typing import Any

ITEM = re.compile(r"^[-*]\s+\[([ xX])\]\s+(\S.*)")


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


def items(path: Path) -> list[dict[str, Any]]:
    return [{
        "index":
        index,
        "key":
        key(block),
        "title":
        ITEM.match(block["lines"][0])[2],
        "details":
        "\n".join(line[2:] if line.startswith("  ") else line for line in block["lines"][1:])
    } for index, block in enumerate(block for block in blocks(path) if block["open"])]


def render(values: list[dict[str, Any]]) -> bytes:
    return ("\n".join(line for block in values for line in block["lines"]) + "\n").encode("utf-8")
