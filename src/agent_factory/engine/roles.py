"""Provider-aware role lookup and safe frontmatter parsing (plan §5.3)."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from agent_factory import __version__

Provider = Literal["claude", "codex"]
RoleType = Literal["task", "review", "approval"]
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "roles"


class RoleError(ValueError):
    pass


class Frontmatter(BaseModel):
    # v1's output/tools/model/name and eject provenance are accepted, not interpreted.
    model_config = ConfigDict(strict=True, extra="ignore")

    type: RoleType
    contract: Literal["spec"] | None = None
    description: str = ""

    @model_validator(mode="after")
    def spec_is_task(self) -> "Frontmatter":
        if self.contract == "spec" and self.type != "task":
            raise ValueError("contract: spec requires type: task")
        return self


@dataclass(frozen=True)
class Role:
    name: str
    provider: Provider
    type: RoleType
    contract: str
    description: str
    text: str
    instructions: str
    path: Path
    source: str
    sha256: str


def resolve_role(name: str, provider: Provider, *, root: str | Path,
                 builtin_dir: Path | None = None) -> Role:
    """Resolve the first existing file; an invalid override never falls through."""
    if not NAME.fullmatch(name):
        raise RoleError(f"Invalid role name: {name!r}")
    if provider not in {"claude", "codex"}:
        raise RoleError(f"Unknown provider: {provider!r}")
    repo_roles = Path(root).resolve() / "factory" / "roles"
    builtins = BUILTIN_DIR if builtin_dir is None else Path(builtin_dir).resolve()
    paths = [repo_roles / f"{name}.{provider}.md", repo_roles / f"{name}.md",
             builtins / f"{name}.{provider}.md", builtins / f"{name}.md"]
    for index, path in enumerate(paths):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RoleError(f"Cannot read role {path}: {exc}") from exc
        try:
            text = data.decode("utf-8-sig")
            lines = text.splitlines(keepends=True)
            if not lines or lines[0].strip() != "---":
                raise ValueError("role needs YAML frontmatter")
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
            if end is None:
                raise ValueError("unterminated YAML frontmatter")
            header = "".join(lines[1:end])
            metadata = yaml.safe_load(header)
            if not isinstance(metadata, dict):
                raise ValueError("frontmatter must be a mapping")
            # safe_load permits duplicate keys; reject ambiguous role contracts.
            node = yaml.compose(header)
            keys = [key.value for key, _ in node.value]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate frontmatter key")
            frontmatter = Frontmatter.model_validate(metadata)
            instructions = "".join(lines[end + 1:])
            if not instructions.strip():
                raise ValueError("role instructions are empty")
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise RoleError(f"Invalid role {path}: {exc}") from exc
        contract = frontmatter.contract or {"task": "status", "review": "verdict",
                                           "approval": "decision"}[frontmatter.type]
        source = str(path) if index < 2 else f"built-in@{__version__}"
        return Role(name, provider, frontmatter.type, contract, frontmatter.description,
                    text, instructions, path, source, hashlib.sha256(data).hexdigest())
    raise RoleError(f"Cannot resolve role {name!r} for {provider}. Paths tried:\n"
                    + "\n".join(str(path) for path in paths))
