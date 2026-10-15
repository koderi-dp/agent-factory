"""Codex exec JSONL adapter, based on the recorded Phase 0 evidence.

Stdout and stderr share events.jsonl. Diagnostics must therefore tolerate plain
text, empty streams (startup failures) and recoverable intermediate errors.
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
    ("auth", r"\b401\b|unauthorized|invalid[_ ]api[_ ]key|missing bearer|authentication (?:failed|required)|not (?:logged|signed) in|please (?:log|sign) in"),
    ("budget", r"(?:max(?:imum)?[-_ ]budget|budget (?:exceeded|exhausted)|exceeded.{0,20}budget)"),
    ("rate_limit", r"\b429\b|rate[-_ ]limit|overloaded|quota (?:exceeded|exhausted)|usage limit"),
    ("permission", r"policy forbids|permission[_ ]denied|approval.{0,30}(?:denied|rejected)|rejected.{0,60}(?:policy|permission)|denied by (?:policy|automatic review)"),
)


def _error_class(text: str) -> str | None:
    return next((kind for kind, pattern in _ERRORS if re.search(pattern, text, re.IGNORECASE)), None)


def _decode(line: bytes) -> Mapping[str, Any] | None:
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    return event if isinstance(event, dict) else None


def _message(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("message", ""))
    return value if isinstance(value, str) else ""


def _diagnostic(event: Mapping[str, Any]) -> str:
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type in {"error", "turn.failed", "wrapper.error"}:
        return _message(event.get("error")) or _message(event)
    item = event.get("item")
    if isinstance(item, Mapping) and item.get("type") == "error":
        return _message(item)
    if isinstance(item, Mapping) and item.get("status") in ("failed", "denied"):
        return _message(item.get("error"))
    return ""


def _path(path: str, worktree: Path | None) -> str:
    value = path.replace("\\", "/")
    if worktree is not None:
        root = str(worktree).replace("\\", "/").rstrip("/")
        # Windows paths are case-insensitive even when logs are read on POSIX.
        windows = bool(re.match(r"^[A-Za-z]:", root))
        a, b = (value.casefold(), root.casefold()) if windows else (value, root)
        if a.startswith(b + "/"):
            return value[len(root) + 1:]
    return value


def _same_path(path: str, expected: Path | None, worktree: Path | None) -> bool:
    if expected is None:
        return False
    left, right = _path(path, worktree), _path(str(expected), worktree)
    return left == right or (bool(re.match(r"^[A-Za-z]:", str(worktree))) and left.casefold() == right.casefold())


def _shell_command(command: str) -> str:
    # exec JSONL includes a shell launcher, especially PowerShell on Windows.
    match = re.search(r"\s(?:-Command|-c|-lc)\s+(['\"])(.*)\1$", command, re.DOTALL | re.IGNORECASE)
    return match[2] if match else command


def _head(worktree: Path) -> str:
    return subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                                   text=True, encoding="utf-8").strip()


class CodexAdapter:
    def __init__(self, settings: MachineSettings | None = None, *,
                 prompt_builder: Callable[[Claim], Prompt] | None = None,
                 head_reader: Callable[[Path], str] = _head) -> None:
        self.settings = settings or MachineSettings()
        self.prompt_builder = prompt_builder
        self.head_reader = head_reader

    def command(self, context: Claim) -> dict[str, Any]:
        execution, attempt = context.execution, context.attempt
        flag = {"auto": "--approve-for-me", "bypass": "--dangerously-bypass-approvals-and-sandbox"}[
            context.job["frozen"]["permissions"]]
        argv = [self.settings.cli.codex or shutil.which("codex") or "codex", "exec", "--json", "-m", execution["model"],
                "-c", "model_reasoning_effort=" + json.dumps(execution["effort"]),
                "-C", context.job["worktree"], "--add-dir",
                str(context.root / "factory/jobs" / context.job["id"]), flag]
        if attempt["kind"] == "resume":
            if not attempt["session_id"]:
                raise ValueError("Codex resume requires the exact thread.started session id")
            # Parent-only flags precede the subcommand; never use --last.
            argv += ["resume", attempt["session_id"]]
        argv.append("-")
        return {"argv": argv, "cwd": context.job["worktree"], "stdin": "prompt.md",
                "env": {"PYTHONUTF8": "1"}}

    def prepare(self, context: Claim) -> Prepared:
        if self.prompt_builder is None:
            raise ValueError("Codex requires a packet builder (plan §8.2, session 1.8)")
        files = self.prompt_builder(context)
        packet = dict(files.packet)
        prompt = files.text
        if context.attempt["kind"] != "resume":
            prompt = files.system + "\n\n" + prompt
        if context.previous and context.attempt["kind"] == "contract_retry":
            error = mask_secrets(context.previous.get("error_detail") or "Report missing or invalid")
            packet["validation_error"] = error
            prompt += "\n\nReport validation error from the previous attempt:\n" + error
        elif context.previous and context.previous["state"] == "lost" and context.attempt["kind"] == "fresh":
            warning = "The worktree may contain partial uncommitted work. Inspect it first."
            packet["retry_context"] = warning
            prompt += "\n\n" + warning
        return Prepared(self.command(context), prompt, files.system, packet)

    def parse_event(self, line: bytes, *, context: Claim | None = None,
                    quiet_seconds: float = 0) -> EventUpdate:
        event = _decode(line)
        if event is None:
            text = line.decode("utf-8", errors="replace")
            kind = _error_class(text)
            safe = mask_secrets(text.strip())
            return EventUpdate(errors=(safe,) if kind else (),
                               permission_denials=(safe,) if kind == "permission" else ())
        session = event.get("thread_id") if event.get("type") == "thread.started" else None
        usage = None
        raw = event.get("usage")
        if event.get("type") == "turn.completed" and isinstance(raw, Mapping):
            def count(key: str) -> int | None:
                value = raw.get(key)
                return value if type(value) is int and value >= 0 else None
            usage = {"input": count("input_tokens"), "cached_input": count("cached_input_tokens"),
                     "output": count("output_tokens"), "cost_usd": None, "raw": [dict(raw)]}
        diagnostic = _diagnostic(event)
        denied = event.get("permission_denials", [])
        denials = [mask_secrets(json.dumps(value, ensure_ascii=False)) for value in denied] if isinstance(denied, list) else []
        if _error_class(diagnostic) == "permission":
            item = event.get("item", {})
            command = item.get("command", "") if isinstance(item, Mapping) else ""
            command = command if isinstance(command, str) else ""
            denials.append(mask_secrets(diagnostic + ("; command: " + command if command else "")))
        return EventUpdate(session if isinstance(session, str) else None, usage,
                           self.summarize(event, worktree=Path(context.job["worktree"]) if context else None,
                                          report_path=context.directory / "report.md" if context else None,
                                          quiet_seconds=quiet_seconds),
                           (mask_secrets(diagnostic),) if diagnostic else (), tuple(denials))

    def summarize(self, event: Mapping[str, Any], *, worktree: Path | None = None,
                  report_path: Path | None = None, quiet_seconds: float = 0) -> str | None:
        item = event.get("item")
        if not isinstance(item, Mapping) or event.get("type") not in ("item.started", "item.updated", "item.completed"):
            return None
        kind = item.get("type")
        if not isinstance(kind, str):
            return None
        summary = None
        if kind == "command_execution" and isinstance(item.get("command"), str):
            command = mask_secrets(_shell_command(item["command"]))
            first = command.splitlines()[0] if command.splitlines() else ""
            if report_path and (str(report_path) in command or _path(str(report_path), worktree) in command) and re.search(
                    r"(?:>|Out-File|Set-Content|Add-Content|tee\b)", command, re.IGNORECASE):
                summary = "writing report"
            elif re.search(r"\bagent-browser\b", first):
                opened = re.search(r"\bopen\s+['\"]?(https?://[^\s'\"]+)", first)
                summary = "opening " + opened[1] if opened else "checking " + first.split("agent-browser", 1)[1].strip()
            elif re.search(r"\bgit\s+commit\b", first):
                subject = re.search(r'''(?:-m|--message)(?:=|\s+)(?:"([^"]*)"|'([^']*)'|(\S+))''', first)
                summary = 'committing "' + next(v for v in subject.groups() if v is not None) + '"' if subject else 'committing'
            else:
                summary = "running `" + (first[:79] + "…" if len(first) > 80 else first) + "`"
        elif kind == "file_change":
            changes = item.get("changes", [])
            paths = list(dict.fromkeys(c["path"] for c in changes if isinstance(c, Mapping) and isinstance(c.get("path"), str))) if isinstance(changes, list) else []
            if any(_same_path(path, report_path, worktree) for path in paths):
                summary = "writing report"
            elif len(paths) == 1:
                summary = "editing " + _path(paths[0], worktree)
            elif paths:
                summary = f"editing {len(paths)} files"
        elif kind in {"file_read", "read", "file_search", "search"}:
            if isinstance(item.get("pattern"), str):
                summary = 'searching "' + item["pattern"] + '"'
            elif isinstance(item.get("path"), str):
                summary = "reading " + _path(item["path"], worktree)
        elif kind == "agent_message" and quiet_seconds >= 20:
            summary = "thinking"
        # Whole command/file fields on updates naturally replace the same item's
        # started activity. An update containing only an ID leaves it untouched.
        return mask_secrets(summary).splitlines()[0] if summary else None

    def classify(self, context: Claim, exit_info: Mapping[str, Any]) -> ExitOutcome:
        if exit_info.get("reason") == "timeout":
            return ExitOutcome("stopped", "timeout", "Execution timed out")
        diagnostics: list[str] = []
        denials: list[str] = []
        terminal = ""
        failed = False
        try:
            with (context.directory / "events.jsonl").open("rb") as stream:
                for line in stream:
                    update = self.parse_event(line)
                    diagnostics.extend(update.errors)
                    denials.extend(update.permission_denials)
                    event = _decode(line)
                    if event and event.get("type") == "turn.failed":
                        failed, terminal = True, _diagnostic(event)
                    elif event and event.get("type") == "turn.completed":
                        failed, terminal = False, ""  # recovered after a previous failure
        except FileNotFoundError:
            pass
        if exit_info["exit_code"] != 0 or failed:
            # A terminal reason outranks earlier reconnect/retry noise.
            kind = _error_class(terminal) or ("permission" if denials else None)
            kind = kind or _error_class("\n".join(diagnostics)) or "crash"
            detail = "\n".join(denials if kind == "permission" else ([terminal] if terminal else diagnostics))
            return ExitOutcome("failed", kind, mask_secrets(detail or f"Codex exited {exit_info['exit_code']}"))
        step = next(s for s in context.job["frozen"]["pipeline"] if s["id"] == context.execution["step_id"])
        entry = next(e for e in step["executions"] if e["id"] == context.execution["exec_id"])
        try:
            result = reports.read_result(context.directory / "report.md", entry["role"]["contract"],
                                         t_launch=context.attempt["t_launch"], on_changes=bool(step["onChanges"]))
        except reports.ReportError as error:
            return ExitOutcome("invalid", "contract", mask_secrets(str(error)))
        # TODO (plan §7.2/§11, session 1.8): enforce git cleanliness, review head
        # immutability and builder commits in the shared git/report validator.
        try:
            head = self.head_reader(Path(context.job["worktree"]))
        except (OSError, subprocess.CalledProcessError) as error:
            return ExitOutcome("failed", "crash", mask_secrets(f"Cannot read worktree HEAD: {error}"))
        return ExitOutcome("valid", completion=pipeline.Completion(
            result.verdict, head, context.attempt["dir"] + "/report.md"), current_head=head)
