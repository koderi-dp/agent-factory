"""Deterministic execution handoffs, with a 64 KiB packet budget (§8)."""

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from agent_factory.engine import gitops, pipeline, reports
from agent_factory.engine.adapters.base import Prompt, mask_secrets
from agent_factory.engine.scheduler import Claim

MAX_PACKET_BYTES = 64 * 1024
MAX_DIFF_BYTES = 24 * 1024

# No job paths, ports, timestamps or provider sessions belong in this prefix.
PREAMBLE_VERSION = "1"
PREAMBLE = """You are executing one agent-factory pipeline execution.
Start from the execution packet. Read further files only when you need them.
Do not re-read earlier rounds' reports unless the packet points you to them.
Follow the project's AGENTS.md or CLAUDE.md, loaded from the worktree by your CLI.
Work only in the job worktree. Never touch the main checkout. Never push.
The first task performs the project's fresh-checkout setup when needed.
Write the report to the absolute report path in the packet, outside the worktree.
First line: task STATUS: READY|BLOCKED (spec: # heading), review VERDICT: PASS|CHANGES,
approval APPROVE|ESCALATE (CHANGES only when the step has onChanges).
READY tasks must leave a clean worktree. The builder must commit its changes.
Reviews and approvals must leave the branch head unchanged and the worktree clean.
Use only the assigned ports. Stop all servers and processes you start before returning.
"""


class PacketError(ValueError):
    pass


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def system_text(frozen: Mapping[str, Any], role: Mapping[str, Any]) -> str:
    """Use frozen inputs only; binary writes preserve the same bytes on Windows."""
    return _lf(frozen["preamble"]).rstrip() + "\n\n" + _lf(role["instructions"]).strip() + "\n"


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise PacketError(f"Cannot read packet source {path}: {error}") from error


def _prefix(text: str, size: int) -> str:
    return text.encode("utf-8")[:max(0, size)].decode("utf-8", errors="ignore")


def _marker(label: str, path: str | Path) -> str:
    return f"[omitted: {label}; see {path}]"


def branch_state(job: Mapping[str, Any]) -> str:
    """Full small diffs; bounded per-file patches for large diffs, no model IO."""
    worktree = Path(job["worktree"])
    current = gitops.branch_head(job)
    revision = f"{job['base_head']}..{current}"
    log = gitops.checked("log", "--oneline", "-51", revision, cwd=worktree).splitlines()
    log_text = "\n".join(log[:50])
    if len(log) > 50:
        log_text += "\n" + _marker("older commits", worktree)
    stat = gitops.checked("diff", "--stat", revision, cwd=worktree)
    diff = gitops.checked("diff", "--no-ext-diff", "--no-color", revision, cwd=worktree)
    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        names = gitops.checked("diff", "--name-only", "-z", revision, cwd=worktree).split("\0")
        names = [name for name in names if name]
        chunks = []
        # Share the budget so one huge generated file doesn't hide every other
        # file's hunks. Mark every file whose patch is not present in full.
        budget = MAX_DIFF_BYTES // max(1, len(names))
        for name in names:
            patch = gitops.checked("diff", "--no-ext-diff", "--no-color", revision, "--", name, cwd=worktree)
            if len(patch.encode("utf-8")) > budget:
                marker = _marker("remaining diff hunks", worktree / name)
                patch = _prefix(patch, budget - len(marker.encode("utf-8")) - 2) + "\n" + marker
            chunks.append(patch)
        diff = "\n".join(chunks)
    return f"{revision}\n\nCommits (at most 50):\n{log_text}\n\nDiff stat:\n{stat}\n\nDiff:\n{diff}"


_SECTIONS = (
    ("job", "Job"), ("paths", "Paths"), ("request", "Request"),
    ("acceptance_criteria", "Acceptance criteria"), ("branch_state", "Branch state"),
    ("upstream_results", "Upstream results (current round)"),
    ("open_findings", "Open findings"), ("retry_context", "Retry context"),
    ("omitted_material", "Omitted material"),
)


def render(packet: Mapping[str, Any]) -> str:
    sections = []
    for key, title in _SECTIONS:
        content = packet[key]
        if isinstance(content, Mapping):
            content = "\n".join(f"- {name}: {value}" for name, value in content.items())
        elif isinstance(content, list):
            content = "\n".join("- " + str(value) for value in content)
        sections.append(f"## {title}\n{content or '(none)'}\n")
    return "\n".join(sections)


def _fit(packet: dict[str, Any], sources: Mapping[str, str]) -> None:
    """Trim branch state first, then handoffs. Mandatory content is never cut.

    Bound both saved packet.json and its rendered Markdown. If the protected
    sections alone exceed the budget, fail explicitly before spawning a CLI.
    """
    def size() -> int:
        return max(len(render(packet).encode("utf-8")),
                   len(json.dumps(packet, ensure_ascii=False, indent=2).encode("utf-8")) + 1)

    for key in ("branch_state", "upstream_results"):
        if size() <= MAX_PACKET_BYTES:
            return
        original = packet[key]
        marker = _marker(key.replace("_", " "), sources[key])
        if sources[key] not in packet["omitted_material"]:
            packet["omitted_material"].append(sources[key])
        packet[key] = marker
        if size() > MAX_PACKET_BYTES:
            continue
        low, high = 0, len(original.encode("utf-8"))
        while low < high:
            middle = (low + high + 1) // 2
            packet[key] = _prefix(original, middle) + "\n" + marker
            if size() <= MAX_PACKET_BYTES:
                low = middle
            else:
                high = middle - 1
        packet[key] = _prefix(original, low) + "\n" + marker
    if size() > MAX_PACKET_BYTES:
        raise PacketError("Packet exceeds 64 KiB after trimming branch state and upstream results; "
                          "reduce the request, acceptance criteria, findings or retry context")


def build(context: Claim, *, executions: Sequence[Mapping[str, Any]] | None = None) -> Prompt:
    job, execution, attempt = context.job, context.execution, context.attempt
    frozen = pipeline._frozen(job)
    step = next(s for s in frozen["pipeline"] if s["id"] == execution["step_id"])
    entry = next(e for e in step["executions"] if e["id"] == execution["exec_id"])
    folder = (context.root / "factory/jobs" / job["id"]).resolve()
    report_path = (context.directory / "report.md").resolve()
    if not report_path.is_relative_to(folder):
        raise PacketError(f"Report destination escapes the job folder: {report_path}")
    if execution["type"] != "task":
        current = gitops.branch_head(job, clean=True)
        if current != execution["head_before"]:
            raise PacketError("the branch changed since this review/approval was dispatched")
    if executions is None:
        connection = pipeline.open_repo(context.root)
        try:
            _, executions = pipeline.load(connection, job["id"])
        finally:
            connection.close()
    done = [row for row in executions if row["result"] and row["report_path"]]
    texts = {}
    omitted = []
    for row in done:
        source = (folder / row["report_path"]).resolve()
        if not source.is_relative_to(folder):
            raise PacketError(f"Report path escapes the job folder: {source}")
        text = _read(source)
        texts[row["id"]] = (source, text, reports.extract_sections(text))
        omitted.append(str(source))
    acceptance = []
    builder_acceptance = []
    contracts = {(s["id"], e["id"]): e["role"]["contract"]
                 for s in frozen["pipeline"] for e in s["executions"]}
    # Surviving spec evidence can precede the current round after a build rewind.
    for row in done:
        if row["state"] != "done":
            continue
        source, _, sections = texts[row["id"]]
        is_spec = contracts.get((row["step_id"], row["exec_id"])) == "spec" or row["uses"] == "spec-writer"
        name = "Acceptance criteria" if is_spec else (
            "Acceptance" if row["uses"] == "builder" and execution["type"] == "approval" else None)
        if name and name in sections:
            target = acceptance if name == "Acceptance criteria" else builder_acceptance
            target.append(f"From {source}:\n{sections[name]}")
    if not acceptance:
        acceptance = builder_acceptance
    upstream = []
    findings = []
    for row in done:
        source, text, sections = texts[row["id"]]
        label = f"{row['step_id']}/{row['exec_id']} ({row['uses']})"
        if row["state"] == "done" and row["round"] == execution["round"]:
            parts = [f"{label}: {row['result']}\nReport: {source}"]
            parts += [f"### {name}\n{sections[name]}" for name in ("Decisions", "Checks run") if name in sections]
            if not sections:
                parts.append(_marker("custom report has no handoff sections", source))
            upstream.append("\n".join(parts))
        if (execution["uses"] == "builder" and execution["round"] > 1
                and row["round"] == execution["round"] - 1 and row["result"] == "CHANGES"):
            # Custom roles without Findings still pass along every CHANGES
            # detail; superseded previous-round evidence must remain usable.
            findings.append(f"### {label}\nReport: {source}\n{sections.get('Findings', text)}")
    request = _read(folder / "request.md")
    reworks = [path for path in folder.glob("rework-*.md") if re.fullmatch(r"rework-[0-9]+", path.stem)]
    if reworks:
        latest = max(reworks, key=lambda path: int(path.stem.split("-")[1]))
        request += f"\n\nLatest rework ({latest}):\n" + _read(latest)
    retry = ""
    if context.previous and attempt["kind"] == "contract_retry":
        retry = "Report validation error: " + mask_secrets(context.previous.get("error_detail") or "Report missing or invalid")
    elif context.previous and attempt["kind"] == "fresh" and context.previous["state"] == "lost":
        retry = "The worktree may hold partial uncommitted work from the lost attempt. Inspect it first."
    packet = {
        "job": {"id": job["id"], "title": job["title"], "pipeline": job["pipeline_name"],
                "step": execution["step_id"], "execution": execution["exec_id"],
                "round": execution["round"], "attempt_kind": attempt["kind"], "onChanges": step["onChanges"]},
        "paths": {"worktree": str(Path(job["worktree"]).resolve()), "branch": job["branch"],
                  "job_folder": str(folder), "report_path": str(report_path),
                  "ports": ", ".join(map(str, context.ports)) or "none",
                  "project_instructions": "AGENTS.md / CLAUDE.md in the worktree (loaded by the CLI)"},
        "request": request, "acceptance_criteria": "\n\n".join(acceptance),
        "branch_state": branch_state(job), "upstream_results": "\n\n".join(upstream),
        "open_findings": "\n\n".join(findings), "retry_context": retry,
        "omitted_material": list(dict.fromkeys(omitted)),
    }
    _fit(packet, {"branch_state": job["worktree"], "upstream_results": str(folder / "executions")})
    text = render(packet)
    if attempt["kind"] == "resume":
        # Resume already has the original packet. Keep the handoff short and
        # point at the new report, never at a previous attempt's report path.
        text = f"Continue {job['id']} {execution['step_id']}/{execution['exec_id']}.\nWrite the report to {report_path}.\n"
    return Prompt(system_text(frozen, entry["role"]), text, packet)
