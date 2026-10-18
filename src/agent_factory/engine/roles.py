"""Provider-aware role lookup and safe frontmatter parsing (plan §5.3)."""

import hashlib
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from agent_factory import __version__

Provider = Literal["claude", "codex", "opencode"]
RoleType = Literal["task", "review", "approval"]
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "roles"


class RoleError(ValueError):
    pass


class Frontmatter(BaseModel):
    # v1's output/tools/model/name are accepted, not interpreted.
    model_config = ConfigDict(strict=True, extra="ignore")

    type: RoleType
    contract: Literal["spec"] | None = None
    description: str = ""
    ejectedFrom: str | None = None

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
    ejected_from: str | None = None


def resolve_role(name: str,
                 provider: Provider,
                 *,
                 root: str | Path,
                 builtin_dir: Path | None = None,
                 builtin_only: bool = False,
                 shared_only: bool = False) -> Role:
    """Resolve the first existing file; an invalid override never falls through."""
    if not NAME.fullmatch(name):
        raise RoleError(f"Invalid role name: {name!r}")
    if provider not in get_args(Provider):
        raise RoleError(f"Unknown provider: {provider!r}")
    repo_roles = Path(root).resolve() / "factory" / "roles"
    builtins = BUILTIN_DIR if builtin_dir is None else Path(builtin_dir).resolve()
    paths = [
        repo_roles / f"{name}.{provider}.md", repo_roles / f"{name}.md",
        builtins / f"{name}.{provider}.md", builtins / f"{name}.md"
    ]
    if builtin_only:
        paths = paths[2:]
    if shared_only:
        paths = [path for path in paths if path.name == f"{name}.md"]
    for path in paths:
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
        contract = frontmatter.contract or {
            "task": "status",
            "review": "verdict",
            "approval": "decision"
        }[frontmatter.type]
        source = str(path) if path.parent == repo_roles else f"built-in@{__version__}"
        return Role(name, provider, frontmatter.type, contract, frontmatter.description, text,
                    instructions, path, source,
                    hashlib.sha256(data).hexdigest(), frontmatter.ejectedFrom)
    raise RoleError(f"Cannot resolve role {name!r} for {provider}. Paths tried:\n" +
                    "\n".join(str(path) for path in paths))


_TYPE_ORDER = {"task": 0, "review": 1, "approval": 2}


def available_roles(root: str | Path) -> dict[str, list[dict[str, str | bool]]]:
    """Every role a stage could use, per provider: the built-ins and the repository's own.

    A role that fails to load is left out of the list rather than failing the whole call;
    `resolve_role` still reports the exact error when a stage names it.
    """
    repo_roles = Path(root).resolve() / "factory" / "roles"
    providers = get_args(Provider)
    names = set()
    for directory in (BUILTIN_DIR, repo_roles):
        for path in directory.glob("*.md") if directory.is_dir() else []:
            stem = path.stem
            for provider in providers:
                stem = stem.removesuffix(f".{provider}")
            if NAME.fullmatch(stem):
                names.add(stem)
    result: dict[str, list[dict[str, str | bool]]] = {provider: [] for provider in providers}
    for provider in providers:
        for name in sorted(names):
            try:
                role = resolve_role(name, provider, root=root)
            except RoleError:
                continue
            result[provider].append({
                "name": role.name,
                "type": role.type,
                "description": role.description,
                "custom": role.path.parent == repo_roles
            })
        result[provider].sort(key=lambda item: (_TYPE_ORDER[str(item["type"])], str(item["name"])))
    return result


def provenance(role: Role, *, root: Path) -> dict[str, str | bool | None]:
    """Compare eject provenance, not customised text, with today's built-in."""
    origin = role.ejected_from
    outdated = False
    if origin:
        try:
            builtin = resolve_role(role.name,
                                   role.provider,
                                   root=root,
                                   builtin_only=True,
                                   shared_only=role.path.name == f"{role.name}.md")
        except RoleError:
            outdated = True
        else:
            digest = origin.rsplit("#", 1)[-1]
            outdated = not (len(digest) >= 8 and builtin.sha256.startswith(digest))
    return {
        "ejectedFrom":
        origin,
        "outdated":
        outdated,
        "status": ("ejected; built-in has changed since" if outdated else "ejected") if origin else
        ("built-in" if role.source.startswith("built-in@") else "override")
    }


def eject(root: Path, name: str, provider: Provider | None = None, *, diff: bool = False) -> str:
    # Without --provider eject the shared source; never copy one provider's
    # instructions into a shared override.
    builtin = resolve_role(name,
                           provider or "codex",
                           root=root,
                           builtin_only=True,
                           shared_only=provider is None)
    target = root / "factory/roles" / (f"{name}.{provider}.md" if provider else f"{name}.md")
    if diff:
        if not target.is_file():
            raise RoleError(f"No ejected role at {target}")
        text = target.read_text(encoding="utf-8-sig")
        # Provenance is metadata, not a local instruction change.
        text = re.sub(r"(?m)^ejectedFrom:.*\n", "", text)
        return "".join(
            difflib.unified_diff(text.splitlines(keepends=True),
                                 builtin.text.replace("\r\n", "\n").splitlines(keepends=True),
                                 fromfile=str(target),
                                 tofile=str(builtin.path)))
    target.parent.mkdir(parents=True, exist_ok=True)
    text = builtin.text.replace("\n", f"\nejectedFrom: {name}@{__version__}#{builtin.sha256}\n", 1)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError as exc:
        raise RoleError(f"Role already exists: {target}; use --diff to compare") from exc
    return f"wrote: {target}"
