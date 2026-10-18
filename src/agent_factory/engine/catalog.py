"""The models and effort levels each installed tool offers, read from the tool itself.

Only metadata commands run here (`codex debug models`, `opencode models`); neither calls a
model, so nothing is billed. Results are cached because OpenCode starts a private server
to answer.
"""

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any

CACHE_SECONDS = 600
TIMEOUTS = {"codex": 20, "opencode": 40}
CLAUDE_MODELS = ("opus", "sonnet", "haiku")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# OpenCode lists models, not the effort variants each one defines.
OPENCODE_EFFORTS = ("low", "medium", "high")
_MODEL_LINE = re.compile(r"^[A-Za-z0-9._-]+/\S+$")

Runner = Callable[[list[str], int], str]
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _run(command: list[str], timeout: int) -> str:
    result = subprocess.run(command,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=timeout,
                            stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:200] or "command failed")
    return result.stdout


def _model(model_id: str, efforts: list[str], default: str | None = None) -> dict[str, Any]:
    return {"id": model_id, "efforts": efforts, "defaultEffort": default}


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def parse_codex(text: str) -> dict[str, Any]:
    """Visible models from `codex debug models`, each with the efforts it supports."""
    models = []
    for entry in json.loads(text)["models"]:
        if entry.get("visibility") == "hide":
            continue
        efforts = [level["effort"] for level in entry.get("supported_reasoning_levels", [])]
        models.append(_model(entry["slug"], efforts, entry.get("default_reasoning_level")))
    if not models:
        raise ValueError("the catalog lists no models")
    return {
        "source": "cli",
        "models": models,
        "efforts": _unique([effort for model in models for effort in model["efforts"]]),
        "detail": None,
    }


def parse_opencode(text: str) -> dict[str, Any]:
    """`provider/model` lines from `opencode models`."""
    ids = _unique([line.strip() for line in text.splitlines() if _MODEL_LINE.match(line.strip())])
    if not ids:
        raise ValueError("it listed no models")
    efforts = list(OPENCODE_EFFORTS)
    return {
        "source": "cli",
        "models": [_model(model_id, efforts) for model_id in ids],
        "efforts": efforts,
        "detail": None,
    }


def _builtin(models: list[str], efforts: tuple[str, ...], detail: str) -> dict[str, Any]:
    return {
        "source": "builtin",
        "models": [_model(model, list(efforts)) for model in models],
        "efforts": list(efforts),
        "detail": detail,
    }


def _ask(name: str, path: str | None, run: Runner) -> dict[str, Any]:
    efforts = CLAUDE_EFFORTS if name == "claude" else OPENCODE_EFFORTS
    if name == "claude":
        return _builtin(list(CLAUDE_MODELS), CLAUDE_EFFORTS,
                        "Claude Code has no command that lists its models.")
    if not (path and Path(path).is_file()):
        return _builtin([], efforts, f"{name} is not installed.")
    command, parse = {
        "codex": ([path, "debug", "models"], parse_codex),
        "opencode": ([path, "models", "--standalone"], parse_opencode),
    }[name]
    try:
        return parse(run(command, TIMEOUTS[name]))
    except subprocess.TimeoutExpired:
        return _builtin([], efforts, f"{name} did not answer in {TIMEOUTS[name]} seconds.")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        return _builtin([], efforts, f"Could not read {name}'s models: {error}")


def discover(paths: Mapping[str, str | None],
             *,
             refresh: bool = False,
             run: Runner = _run,
             clock: Callable[[], float] = time.monotonic) -> dict[str, dict[str, Any]]:
    """Per tool: its models and efforts, and whether they came from the tool or built-ins.

    `paths` are machine-setting overrides (`cli.<tool>`) that win over PATH. Answers
    from the tool are cached for ten minutes; failures are retried on the next call.
    """
    resolved = {
        name: paths.get(name) or shutil.which(name)
        for name in ("claude", "codex", "opencode")
    }
    now = clock()
    result: dict[str, dict[str, Any]] = {}
    todo = []
    with _lock:
        for name, path in resolved.items():
            hit = _cache.get((name, str(path)))
            if hit and not refresh and now - hit[0] < CACHE_SECONDS:
                result[name] = hit[1]
            else:
                todo.append(name)
    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            answers = dict(zip(todo, pool.map(lambda n: _ask(n, resolved[n], run), todo)))
        with _lock:
            for name, answer in answers.items():
                result[name] = answer
                if answer["source"] == "cli":
                    _cache[(name, str(resolved[name]))] = (clock(), answer)
    return {name: result[name] for name in resolved}
