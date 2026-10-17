"""Validated repo configuration, pure v1 upgrades, and machine settings (§5)."""

import copy
import json
import sqlite3
import warnings
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, ValidationInfo,
                      field_validator, model_validator)

from agent_factory import __version__
from agent_factory.engine import db, versioning
from agent_factory.engine.roles import Provider, Role, resolve_role

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
RoleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Nonempty = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
PositiveMinutes = Annotated[float, Field(gt=0, allow_inf_nan=False)]
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.json"


class ConfigModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)


class Profile(ConfigModel):
    model: Nonempty
    effort: Nonempty


class Execution(ConfigModel):
    id: Identifier
    uses: RoleName
    provider: Provider
    profiles: dict[Provider, Profile]
    timeoutMinutes: PositiveMinutes | None = None
    maxBudgetUsd: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def selected_profile(self) -> "Execution":
        if self.provider not in self.profiles:
            raise ValueError(f"profiles[{self.provider}] needs explicit model and effort")
        if self.provider == "codex" and self.maxBudgetUsd is not None:
            warnings.warn(f"{self.id}: maxBudgetUsd is ignored for Codex",
                          UserWarning,
                          stacklevel=2)
        if self.provider == "opencode":
            if self.maxBudgetUsd is not None:
                warnings.warn(f"{self.id}: maxBudgetUsd is ignored for OpenCode",
                              UserWarning,
                              stacklevel=2)
            # `opencode run --model` needs the CLI's provider/model[#variant] form.
            if "/" not in self.profiles["opencode"].model:
                raise ValueError(f"{self.id}: profiles[opencode].model must be provider/model "
                                 "for OpenCode")
        return self


class SingleStep(Execution):
    label: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    onChanges: Identifier | None = None

    @property
    def executions(self) -> list[Execution]:
        return [self]


class ParallelStep(ConfigModel):
    id: Identifier
    parallel: Annotated[list[Execution], Field(min_length=1, max_length=16)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    onChanges: Identifier | None = None

    @property
    def executions(self) -> list[Execution]:
        return self.parallel


class RepoConcurrency(ConfigModel):
    perJob: Annotated[int, Field(ge=1, le=16)] = 4


class Timeouts(ConfigModel):
    executionMinutes: PositiveMinutes = 60


class RepoConfig(ConfigModel):
    version: Literal[2] = 2
    engine: Nonempty
    defaultPipeline: Identifier
    pipelines: dict[Identifier, Annotated[list[SingleStep | ParallelStep],
                                          Field(min_length=1, max_length=32)]]
    maxRounds: Annotated[int, Field(ge=1, le=5)] = 3
    merge: Literal["auto", "verify"] = "verify"
    permissions: Literal["auto", "bypass"] = "auto"
    concurrency: RepoConcurrency = Field(default_factory=RepoConcurrency)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    sensitivePaths: list[Nonempty] = Field(default_factory=lambda: [
        "**/migrations/**",
        ".github/**",
        "**/*.lock",
        "package.json",
        "pyproject.toml",
        "**/auth/**",
        "Dockerfile",
        "**/*.tf",
    ])
    _roles: dict[tuple[str, str], Role] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def validate_pipelines(self, info: ValidationInfo) -> "RepoConfig":
        if self.defaultPipeline not in self.pipelines:
            raise ValueError("defaultPipeline must name an existing pipeline")
        root = Path((info.context or {}).get("root", Path.cwd()))
        resolved = {}
        for name, steps in self.pipelines.items():
            ids: set[str] = set()
            tasks: set[str] = set()
            kinds = []
            for step in steps:
                # A single step is its own execution; count that id only once.
                entries = [step.id
                           ] + ([e.id
                                 for e in step.parallel] if isinstance(step, ParallelStep) else [])
                for identifier in entries:
                    if identifier in ids:
                        raise ValueError(f"{name}: duplicate step or execution id: {identifier}")
                    ids.add(identifier)
                roles = []
                for execution in step.executions:
                    role = resolve_role(execution.uses, execution.provider, root=root)
                    resolved[name, execution.id] = role
                    roles.append(role)
                types = {role.type for role in roles}
                if isinstance(step, ParallelStep) and types != {"review"}:
                    raise ValueError(f"{name}/{step.id}: parallel groups must contain only reviews")
                kind = roles[0].type
                if step.onChanges is not None and (kind not in {"review", "approval"}
                                                   or step.onChanges not in tasks):
                    raise ValueError(f"{name}/{step.id}: onChanges must name an earlier task step "
                                     "and is allowed only on review or approval steps")
                if kind == "task":
                    tasks.add(step.id)
                kinds.append(kind)
            if kinds[-1] != "approval" or kinds.count("approval") != 1:
                raise ValueError(f"{name}: pipeline must end with exactly one approval step")
        self._roles = resolved
        # engine.pipeline.freeze snapshots these roles and policy at job creation (§10.5).
        # TODO (plan §15.4, session 1.9): enforce the engine version gate in commands.
        return self

    def role_for(self, pipeline: str, execution_id: str) -> Role:
        return self._roles[pipeline, execution_id]


def template_config() -> dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def upgrade_v1(raw: dict[str, Any], *, engine_version: str | None = None) -> dict[str, Any]:
    """Return an independent v2 dictionary without mutating or writing the input."""
    if not isinstance(raw, dict):
        raise ValueError("Config must be an object")
    version = raw.get("version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise ValueError(f"Unsupported config version: {version!r}")
    if version == 2:
        return copy.deepcopy(raw)
    result = copy.deepcopy(raw)
    provider = result.pop("provider", "claude")
    pipeline = result.pop("pipeline", copy.deepcopy(_V1_DEFAULT))
    if not isinstance(pipeline, list):
        raise ValueError("v1 pipeline must be a list")
    for step in pipeline:
        if not isinstance(step, dict):
            raise ValueError("v1 steps must be objects")
        executions = step.get("parallel", [step])
        if not isinstance(executions, list):
            raise ValueError("v1 parallel groups must be lists")
        for execution in executions:
            if not isinstance(execution, dict):
                raise ValueError("v1 executions must be objects")
            execution["provider"] = provider
            if "providers" in execution:
                execution["profiles"] = execution.pop("providers")
    permissions = result.pop("headlessPermissions", "auto")
    result.pop("start", None)
    # v1 defaulted to auto; new v2 repos default to verify in the template.
    result.setdefault("merge", "auto")
    result.update(version=2,
                  engine=engine_range(engine_version),
                  defaultPipeline="full",
                  pipelines={
                      "full": pipeline,
                      "fast": copy.deepcopy(_FAST_DEFAULT)
                  },
                  permissions="bypass" if permissions == "bypassPermissions" else permissions)
    return result


def engine_range(version: str | None = None) -> str:
    """PEP 440 range covering the running engine's major release.

    A prerelease such as ``1.0.0rc1`` sorts below the stable ``>=1.0`` bound, so fall
    back to the running version (minus any local segment) as the lower bound. That keeps
    a repository usable by the engine that created it while the next major stays excluded.
    """
    version = __version__ if version is None else version
    major = int(version.split(".", 1)[0])
    bound = f">={major}.0,<{major + 1}"
    if not versioning.matches(version, bound):
        return f">={version.split('+', 1)[0]},<{major + 1}"
    return bound


# Materialised once so upgrade_v1 is a pure transformation, including the omitted-pipeline case.
_DEFAULTS = template_config()
_FAST_DEFAULT = _DEFAULTS["pipelines"]["fast"]
_V1_DEFAULT = copy.deepcopy(_DEFAULTS["pipelines"]["full"])
for _step in _V1_DEFAULT:
    for _execution in _step.get("parallel", [_step]):
        _execution.pop("provider")
        _execution["providers"] = _execution.pop("profiles")
        if "parallel" in _step:
            _execution["id"] = _execution["uses"]


def validate_config(raw: dict[str, Any], *, root: str | Path) -> RepoConfig:
    return RepoConfig.model_validate(upgrade_v1(raw), context={"root": Path(root)})


def load_config(root: str | Path) -> RepoConfig:
    root = Path(root).resolve()
    raw = json.loads((root / "factory" / "config.json").read_text(encoding="utf-8-sig"))
    return validate_config(raw, root=root)


class GlobalConcurrency(ConfigModel):
    global_: Annotated[int, Field(ge=1, le=32)] = Field(default=6, alias="global")


class CliPaths(ConfigModel):
    claude: str | None = None
    codex: str | None = None
    opencode: str | None = None

    @field_validator("claude", "codex", "opencode")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("CLI path must be absolute")
        return value


class Ports(ConfigModel):
    range: str = "5100-5999"

    @field_validator("range")
    @classmethod
    def valid_range(cls, value: str) -> str:
        parts = value.split("-")
        if len(parts) != 2 or not all(p.isascii() and p.isdecimal() for p in parts):
            raise ValueError("ports.range must be start-end")
        start, end = map(int, parts)
        if not 1 <= start <= end <= 65535:
            raise ValueError("ports.range must satisfy 1 <= start <= end <= 65535")
        return value


class MachineSettings(ConfigModel):
    concurrency: GlobalConcurrency = Field(default_factory=GlobalConcurrency)
    cli: CliPaths = Field(default_factory=CliPaths)
    ports: Ports = Field(default_factory=Ports)


_MACHINE_KEYS = {"concurrency.global", "cli.claude", "cli.codex", "cli.opencode", "ports.range"}


def load_machine_settings(connection: sqlite3.Connection) -> MachineSettings:
    raw: dict[str, Any] = {}
    for key, value in connection.execute("SELECT key, value FROM settings"):
        if key in _MACHINE_KEYS:
            group, field = key.split(".")
            raw.setdefault(group, {})[field] = json.loads(value)
    return MachineSettings.model_validate(raw)


def save_machine_settings(connection: sqlite3.Connection,
                          settings: MachineSettings | dict[str, Any]) -> MachineSettings:
    raw = settings.model_dump(by_alias=True) if isinstance(settings, MachineSettings) else settings
    validated = MachineSettings.model_validate(raw)
    rows = [(f"{group}.{field}", json.dumps(value))
            for group, fields in validated.model_dump(by_alias=True).items()
            for field, value in fields.items()]
    with db.tx(connection):
        connection.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", rows)
    return validated
