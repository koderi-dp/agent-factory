"""OpenCode v2 `run --format json` adapter, based on real CLI captures.

`opencode run` writes one JSON object per stdout line and every line carries the
session id, so resuming never depends on re-deriving state. The wrapper merges
stderr into the same stream, so diagnostics must tolerate plain text as well as
the CLI's own `error` lines. See `tests/fixtures/opencode/README.md`.
"""

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from agent_factory.engine import pipeline, reports
from agent_factory.engine.adapters.base import EventUpdate, ExitOutcome, Prepared, Prompt, mask_secrets
from agent_factory.engine.config import MachineSettings
from agent_factory.engine.scheduler import Claim

_ERRORS = (
    ("auth",
     r"\b401\b|unauthorized|invalid[_ ]api[_ ]key|missing bearer|authentication (?:failed|required)|not (?:logged|signed) in|please (?:log|sign) in|auth[_ ]required"
     ),
    ("budget", r"(?:max(?:imum)?[-_ ]budget|budget (?:exceeded|exhausted)|exceeded.{0,20}budget)"),
    ("rate_limit", r"\b429\b|rate[-_ ]limit|overloaded|quota (?:exceeded|exhausted)|usage limit"),
    ("permission",
     r"policy forbids|permission[_ ]denied|not permitted|approval.{0,30}(?:denied|rejected)|rejected.{0,60}(?:policy|permission)|denied by (?:policy|automatic review)"
     ),
)

_REPORT_REDIRECT = re.compile(r"(?:>|>>|Out-File|Set-Content|Add-Content|tee\b)", re.IGNORECASE)
_COMMIT = re.compile(r'''(?:-m|--message)(?:=|\s+)(?:"([^"]*)"|'([^']*)'|(\S+))''')


def _error_class(text: str) -> str | None:
    return next((kind for kind, pattern in _ERRORS if re.search(pattern, text, re.IGNORECASE)),
                None)


def _decode(line: bytes) -> Mapping[str, Any] | None:
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    return event if isinstance(event, dict) else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("message", "error", "text", "output", "title"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found
    return ""


def _first_string(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _short(path: str, worktree: Path | None) -> str:
    value = path.replace("\\", "/")
    if worktree is not None:
        root = str(worktree).replace("\\", "/").rstrip("/")
        # Windows paths are case-insensitive even when logs are read on POSIX.
        windows = bool(re.match(r"^[A-Za-z]:", root))
        left, right = (value.casefold(), root.casefold()) if windows else (value, root)
        if left.startswith(right + "/"):
            return value[len(root) + 1:]
    return value


def _same_path(path: str, expected: Path | None, worktree: Path | None) -> bool:
    if expected is None:
        return False
    left, right = _short(path, worktree), _short(str(expected), worktree)
    return left == right or (bool(re.match(r"^[A-Za-z]:", str(worktree)))
                             and left.casefold() == right.casefold())


def _clip(text: str) -> str:
    return text[:79] + "…" if len(text) > 80 else text


def _model_ref(execution: Mapping[str, Any]) -> str:
    """`provider/model`, then the profile's effort as its `#variant`.

    An explicit variant in the configured model wins: OpenCode rejects a variant
    a model does not define, so `effort` must name one of the model's variants.
    """
    model = execution["model"]
    if "#" in model:
        return model
    return f"{model}#{execution['effort']}"


class OpencodeAdapter:

    def __init__(self,
                 settings: MachineSettings | None = None,
                 *,
                 prompt_builder: Callable[[Claim], Prompt] | None = None) -> None:
        self.settings = settings or MachineSettings()
        if prompt_builder is None:
            from agent_factory.engine.packet import build
            prompt_builder = build
        self.prompt_builder = prompt_builder

    def command(self, context: Claim) -> dict[str, Any]:
        # The frozen permission policy has one non-interactive equivalent here:
        # `--auto` approves everything a rule did not deny, and OpenCode keeps
        # enforcing configured deny rules and policies either way.
        argv = [
            self.settings.cli.opencode or shutil.which("opencode") or "opencode", "run", "--format",
            "json", "--standalone", "--auto", "--model",
            _model_ref(context.execution)
        ]
        if context.attempt["kind"] == "resume":
            if not context.attempt["session_id"]:
                raise ValueError(
                    "OpenCode resume requires the exact sessionID from the event stream")
            argv += ["--session", context.attempt["session_id"]]
        return {
            "argv": argv,
            "cwd": context.job["worktree"],
            "stdin": "prompt.md",
            "env": {
                "PYTHONUTF8": "1"
            }
        }

    def prepare(self, context: Claim) -> Prepared:
        # Mirrors CodexAdapter.prepare: the packet builder owns sections, and the
        # engine's retry policy is provider-neutral.
        files = self.prompt_builder(context)
        packet = dict(files.packet)
        prompt = files.text
        if context.attempt["kind"] != "resume":
            prompt = files.system + "\n\n" + prompt
        if context.previous and context.attempt["kind"] == "contract_retry" and not packet.get(
                "retry_context"):
            error = mask_secrets(
                context.previous.get("error_detail") or "Report missing or invalid")
            packet["validation_error"] = error
            prompt += "\n\nReport validation error from the previous attempt:\n" + error
        elif (context.previous and context.previous["state"] == "lost"
              and context.attempt["kind"] == "fresh" and not packet.get("retry_context")):
            warning = "The worktree may contain partial uncommitted work. Inspect it first."
            packet["retry_context"] = warning
            prompt += "\n\n" + warning
        return Prepared(self.command(context), prompt, files.system, packet,
                        len(files.text.encode("utf-8")))

    def parse_event(self,
                    line: bytes,
                    *,
                    context: Claim | None = None,
                    quiet_seconds: float = 0) -> EventUpdate:
        event = _decode(line)
        if event is None:
            text = line.decode("utf-8", errors="replace")
            kind = _error_class(text)
            safe = mask_secrets(text.strip())
            return EventUpdate(errors=(safe, ) if kind else (),
                               permission_denials=(safe, ) if kind == "permission" else ())
        session = event.get("sessionID")
        part = event.get("part")
        part = part if isinstance(part, Mapping) else {}
        usage = None
        if event.get("type") == "step_finish":
            usage = _usage(part)
        diagnostic = _diagnostic(event, part)
        denials = (mask_secrets(diagnostic), ) if _error_class(diagnostic) == "permission" else ()
        return EventUpdate(
            session if isinstance(session, str) else None, usage,
            self.summarize(event,
                           worktree=Path(context.job["worktree"]) if context else None,
                           report_path=context.directory / "report.md" if context else None,
                           quiet_seconds=quiet_seconds),
            (mask_secrets(diagnostic), ) if diagnostic else (), denials)

    def summarize(self,
                  event: Mapping[str, Any],
                  *,
                  worktree: Path | None = None,
                  report_path: Path | None = None,
                  quiet_seconds: float = 0) -> str | None:
        part = event.get("part")
        if not isinstance(part, Mapping):
            return None
        summary = None
        if event.get("type") == "tool_use":
            summary = self._tool_summary(part, worktree, report_path)
        elif event.get("type") == "text" and quiet_seconds >= 20:
            # A silent text part is the model thinking, not an action.
            summary = "thinking"
        return mask_secrets(summary).splitlines()[0] if summary else None

    def _tool_summary(self, part: Mapping[str, Any], worktree: Path | None,
                      report_path: Path | None) -> str | None:
        tool = part.get("tool") if isinstance(part.get("tool"), str) else ""
        state = part.get("state")
        state = state if isinstance(state, Mapping) else {}
        data = state.get("input")
        data = data if isinstance(data, Mapping) else {}
        path = _first_string(data, "path", "filePath", "file_path")
        if tool in {"bash", "shell"}:
            command = _first_string(data, "command", "cmd")
            if not command:
                return None
            first = command.splitlines()[0] if command.splitlines() else ""
            redirected = first.replace('"', " ").replace("'", " ").split()
            if (report_path and _REPORT_REDIRECT.search(first)
                    and any(_same_path(token, report_path, worktree) for token in redirected)):
                return "writing report"
            if re.search(r"\bgit\s+commit\b", first):
                subject = _COMMIT.search(first)
                if subject is None:
                    return "committing"
                return 'committing "' + next(
                    value for value in subject.groups() if value is not None) + '"'
            return "running `" + _clip(first) + "`"
        if tool == "read":
            return "reading " + _short(path, worktree) if path else None
        if tool in {"edit", "write", "patch"}:
            if path and _same_path(path, report_path, worktree):
                return "writing report"
            return "editing " + _short(path, worktree) if path else "editing"
        if tool in {"glob", "grep"}:
            pattern = _first_string(data, "pattern", "query")
            return 'searching "' + pattern + '"' if pattern else None
        if tool in {"webfetch", "fetch"}:
            url = _first_string(data, "url")
            return "opening " + url if url else None
        if tool in {"websearch", "web_search"}:
            query = _first_string(data, "query", "pattern")
            return "searching " + query if query else None
        if tool in {"task", "subagent"}:
            return "delegating to " + (_first_string(data, "description", "agent") or "subagent")
        if tool:
            return "using " + tool
        return None

    def classify(self, context: Claim, exit_info: Mapping[str, Any]) -> ExitOutcome:
        if exit_info.get("reason") == "timeout":
            return ExitOutcome("stopped", "timeout", "Execution timed out")
        diagnostics: list[str] = []
        denials: list[str] = []
        try:
            with (context.directory / "events.jsonl").open("rb") as stream:
                for line in stream:
                    update = self.parse_event(line)
                    diagnostics.extend(update.errors)
                    denials.extend(update.permission_denials)
        except FileNotFoundError:
            pass
        if exit_info["exit_code"] != 0:
            # `opencode run` exits non-zero for a failed run and the last error
            # line carries the CLI's own reason (e.g. provider.no-route). A stale
            # error line does not fail a run that exits zero and validates below.
            terminal = diagnostics[-1] if diagnostics else ""
            kind = _error_class("\n".join(denials)) or _error_class(
                terminal or "\n".join(diagnostics)) or "crash"
            detail = "\n".join(denials if kind == "permission" else (
                [terminal] if terminal else diagnostics))
            return ExitOutcome("failed", kind,
                               mask_secrets(detail or f"OpenCode exited {exit_info['exit_code']}"))
        step = next(s for s in context.job["frozen"]["pipeline"]
                    if s["id"] == context.execution["step_id"])
        entry = next(e for e in step["executions"] if e["id"] == context.execution["exec_id"])
        try:
            result = reports.read_result(context.directory / "report.md",
                                         entry["role"]["contract"],
                                         t_launch=context.attempt["t_launch"],
                                         on_changes=bool(step["onChanges"]))
            head = reports.validate_git(result, context.job, context.execution)
        except reports.ReportError as error:
            return ExitOutcome("invalid", "contract", mask_secrets(str(error)))
        except (OSError, subprocess.CalledProcessError) as error:
            return ExitOutcome("failed", "crash",
                               mask_secrets(f"Cannot read worktree HEAD: {error}"))
        return ExitOutcome("valid",
                           completion=pipeline.Completion(result.verdict, head,
                                                          context.attempt["dir"] + "/report.md"),
                           current_head=head)


def _usage(part: Mapping[str, Any]) -> Mapping[str, Any] | None:
    tokens = part.get("tokens")
    if not isinstance(tokens, Mapping):
        return None
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, Mapping) else {}

    def count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    cost = part.get("cost")
    return {
        "input": count(tokens.get("input")),
        "cached_input": count(cache.get("read")),
        "output": count(tokens.get("output")),
        "cost_usd": cost if type(cost) in (int, float) and cost >= 0 else None,
        "raw": [dict(tokens)],
    }


def _diagnostic(event: Mapping[str, Any], part: Mapping[str, Any]) -> str:
    if event.get("type") == "error":
        return _text(event.get("error")) or _text(event)
    state = part.get("state")
    if isinstance(state, Mapping) and state.get("status") == "error":
        return _text(state.get("error")) or _text(state)
    return ""
