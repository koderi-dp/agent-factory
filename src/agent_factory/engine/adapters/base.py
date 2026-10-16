"""Replay-safe provider interface and safe display text (§6.5, §13.4)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal, Protocol

from agent_factory.engine import pipeline
from agent_factory.engine.scheduler import Claim


@dataclass(frozen=True)
class Prompt:
    """Supplied by the packet builder (plan §8.2, session 1.8).

    On resume, text is a short continuation with the new report destination.
    The builder owns packet sections, size limits and stable system assembly.
    """

    system: str
    text: str
    packet: Mapping[str, Any]


@dataclass(frozen=True)
class Prepared:
    command: Mapping[str, Any]
    prompt: str
    system: str
    packet: Mapping[str, Any]
    packet_bytes: int | None = None


@dataclass(frozen=True)
class EventUpdate:
    session_id: str | None = None
    usage: Mapping[str, Any] | None = None
    activity: str | None = None
    errors: tuple[str, ...] = ()
    permission_denials: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExitOutcome:
    state: Literal["valid", "invalid", "failed", "stopped"]
    error_class: str | None = None
    detail: str | None = None
    completion: pipeline.Completion | None = None
    current_head: str | None = None


class Adapter(Protocol):
    """Hooks run outside transactions. No mutable per-attempt parser state.

    Complete byte lines may be replayed after a crash. Classification rereads
    durable evidence, so a new adapter instance can finalise an adopted run.
    Engine timeouts are passed as exit_info['reason'] == 'timeout'.
    """

    def prepare(self, context: Claim) -> Prepared: ...
    def parse_event(self, line: bytes, *, context: Claim | None = None,
                    quiet_seconds: float = 0) -> EventUpdate: ...
    def summarize(self, event: Mapping[str, Any], *, worktree: Path | None = None,
                  report_path: Path | None = None, quiet_seconds: float = 0) -> str | None: ...
    def classify(self, context: Claim, exit_info: Mapping[str, Any]) -> ExitOutcome: ...


class UnsupportedProviderError(ValueError):
    pass


_VALUE = r'''(?:"[^"\r\n]*(?:"|$)|'[^'\r\n]*(?:'|$)|[^\s;&|]+)'''
_SECRET = re.compile(
    r"(?P<prefix>(?:--[\w-]*(?:token|key|secret|password|credential)[\w-]*[=\s]+|"
    r"\b[\w-]*(?:key|token|secret|password|credential)[\w-]*[\"']?[=:\s]*[=:]\s*))" + _VALUE,
    re.IGNORECASE | re.MULTILINE)


def mask_secrets(text: str) -> str:
    text = _SECRET.sub(lambda match: match['prefix'] + '…', text)
    text = re.sub(r"\bBearer\s+[^\s,;]+", "Bearer …", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "…", text)
    return re.sub(r"(?<![\w/+])[A-Za-z0-9_+/-]{48,}={0,2}(?![\w/+])", "…", text)


def add_usage(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    """Sum per-turn observations at the durable offset boundary; never estimate."""
    if previous is None:
        return dict(current)
    result = dict(current)
    for key in ("input", "cached_input", "output", "cost_usd"):
        a, b = previous.get(key), current.get(key)
        result[key] = a + b if a is not None and b is not None else None
    result["raw"] = [*previous.get("raw", []), *current.get("raw", [])]
    return result
