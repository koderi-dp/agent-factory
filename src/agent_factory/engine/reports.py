"""Report contracts, handoff sections and shared acceptance checks (§7.2)."""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping
from typing import Any


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class Report:
    verdict: str
    text: str
    sections: dict[str, str]


def verdict(text: str, contract: str, *, on_changes: bool = False) -> str:
    """Validate the first physical line, preserving the v1 contracts."""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if contract == "spec" and first.startswith("# "):
        return "READY"
    allowed = {
        "status": {"STATUS: READY", "STATUS: BLOCKED"},
        "verdict": {"VERDICT: PASS", "VERDICT: CHANGES"},
        "decision": {"APPROVE", "ESCALATE"} | ({"CHANGES"} if on_changes else set())
    }
    if first in allowed.get(contract, set()):
        return first.split(": ")[-1]
    raise ReportError(f"invalid first line for {contract}: {first!r}")


def extract_sections(text: str) -> dict[str, str]:
    """Extract level-two Markdown sections, ignoring headings in fenced code."""
    sections: dict[str, list[str]] = {}
    current = None
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        fenced = fence is not None
        if marker:
            run, rest = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
        heading = None if fenced or fence else re.match(r"^ {0,3}(#{1,2})[ \t]+(.+?)\s*$", line)
        if heading:
            level, title = heading.groups()
            current = re.sub(r"[ \t]+#+$", "", title) if level == "##" else None
            if current is not None:
                sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {title: "".join(lines).strip() for title, lines in sections.items()}


def parse_report(text: str, contract: str, *, on_changes: bool = False) -> Report:
    # Custom roles can omit handoff sections. Built-in text is rewritten in §17 Phase 2.
    return Report(verdict(text, contract, on_changes=on_changes), text, extract_sections(text))


def read_result(path: str | Path,
                contract: str,
                *,
                t_launch: str | datetime | float,
                on_changes: bool = False) -> Report:
    """Require a report written strictly after launch, then parse its contract."""
    if isinstance(t_launch, str):
        t_launch = datetime.fromisoformat(t_launch.replace("Z", "+00:00"))
    if isinstance(t_launch, datetime):
        if t_launch.tzinfo is None:
            raise ReportError("t_launch must include a timezone")
        t_launch = t_launch.timestamp()
    path = Path(path)
    try:
        # One handle keeps the freshness check and contents on the same file.
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_mtime_ns <= int(t_launch * 1_000_000_000):
                raise ReportError(f"not a fresh report: {path}")
            text = stream.read().decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ReportError(f"cannot read report {path}: {exc}") from exc
    return parse_report(text, contract, on_changes=on_changes)


def validate_git(report: Report, job: Mapping[str, Any], execution: Mapping[str, Any]) -> str:
    """Provider-neutral validation before a completion can reach the pipeline."""
    from agent_factory.engine import gitops
    try:
        return gitops.validate_result(job, execution, report.verdict)
    except gitops.GitError as error:
        raise ReportError(str(error)) from error
