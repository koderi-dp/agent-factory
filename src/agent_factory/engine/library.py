"""Pipelines saved on this machine for reuse across repositories.

A repository never points at the library: adding a library pipeline copies it into the
repository's own config, so a clone works for teammates and CI without this machine.
"""

import copy
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from agent_factory import run_cli
from agent_factory.engine.config import engine_range, template_config, validate_config
from agent_factory.paths import machine_dir

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _path() -> Path:
    return machine_dir() / "pipelines.json"


def _saved() -> dict[str, list[Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read the pipeline library {_path()}: {error}") from error
    return data if isinstance(data, dict) else {}


def builtins() -> dict[str, list[Any]]:
    return template_config()["pipelines"]


def list_pipelines() -> list[dict[str, Any]]:
    """Built-ins first, then saved ones by name. A saved name never hides a built-in."""
    result = [{"name": name, "steps": steps, "builtin": True} for name, steps in builtins().items()]
    taken = {item["name"] for item in result}
    result += [{
        "name": name,
        "steps": steps,
        "builtin": False
    } for name, steps in sorted(_saved().items()) if name not in taken]
    return result


def validate_steps(name: str, steps: list[Any]) -> None:
    """Check a pipeline on its own, against the built-in roles only.

    A library pipeline must work in any repository, so a role that exists only in one
    repository's factory/roles is rejected here with the engine's own message.
    """
    if not NAME.fullmatch(name):
        raise ValueError("Use lowercase letters, digits and dashes, up to 32 characters.")
    raw = template_config()
    raw.update(engine=engine_range(), defaultPipeline=name, pipelines={name: copy.deepcopy(steps)})
    with tempfile.TemporaryDirectory(prefix="factory-library-") as empty:
        validate_config(raw, root=Path(empty))


def save(name: str, steps: list[Any]) -> dict[str, Any]:
    if name in builtins():
        raise ValueError(f"{name} is a built-in pipeline; save under another name.")
    validate_steps(name, steps)
    saved = _saved()
    saved[name] = copy.deepcopy(steps)
    _path().parent.mkdir(parents=True, exist_ok=True)
    run_cli.atomic_json(_path(), saved)
    return {"name": name, "steps": saved[name], "builtin": False}


def delete(name: str) -> None:
    if name in builtins():
        raise ValueError(f"{name} is a built-in pipeline and cannot be deleted.")
    saved = _saved()
    if name not in saved:
        raise KeyError(f"No saved pipeline named {name}")
    del saved[name]
    run_cli.atomic_json(_path(), saved)
