"""Configuration contracts from plan §5.1, §5.2 and §5.4."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_factory import __version__
from agent_factory.engine import db, versioning
from agent_factory.engine.config import (MachineSettings, RepoConfig, engine_range, load_config,
                                         load_machine_settings, save_machine_settings,
                                         template_config, upgrade_v1, validate_config)
from agent_factory.engine.registry import open_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def raw():
    return template_config()


def validate(raw, root):
    return RepoConfig.model_validate(raw, context={"root": root})


def test_template_has_full_and_fast_pipelines(raw, tmp_path):
    config = validate(raw, tmp_path)
    assert config.defaultPipeline == "full"
    assert [s.id for s in config.pipelines["full"]] == ["spec", "build", "review", "approve"]
    assert [s.uses for s in config.pipelines["fast"]] == ["builder", "fast-approver"]
    assert config.pipelines["fast"][-1].onChanges == "build"
    assert config.model_dump(exclude_none=True) == raw


@pytest.mark.parametrize("name", ["", "Upper", "-first", "a_b", "a" * 33, "a/b"])
def test_pipeline_name_pattern(raw, tmp_path, name):
    raw["pipelines"][name] = raw["pipelines"].pop("full")
    raw["defaultPipeline"] = name
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_default_pipeline_must_exist(raw, tmp_path):
    raw["defaultPipeline"] = "missing"
    with pytest.raises(ValidationError, match="defaultPipeline"):
        validate(raw, tmp_path)


@pytest.mark.parametrize("identifier", ["", "Upper", "-first", "a_b", "a" * 33, "a/b"])
@pytest.mark.parametrize("parallel", [False, True])
def test_step_and_execution_id_patterns(raw, tmp_path, identifier, parallel):
    target = raw["pipelines"]["full"][2]
    (target["parallel"][0] if parallel else target)["id"] = identifier
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


@pytest.mark.parametrize(
    "collision",
    ["step-step", "child-child", "child-step", "child-group", "across-groups", "child-later-step"])
def test_ids_are_unique_within_pipeline(raw, tmp_path, collision):
    full = raw["pipelines"]["full"]
    group = full[2]
    if collision == "step-step":
        full[1]["id"] = full[0]["id"]
    elif collision == "child-child":
        group["parallel"][1]["id"] = group["parallel"][0]["id"]
    elif collision == "child-step":
        group["parallel"][0]["id"] = full[0]["id"]
    elif collision == "child-group":
        group["parallel"][0]["id"] = group["id"]
    elif collision == "child-later-step":
        group["parallel"][0]["id"] = full[-1]["id"]
    else:
        later = copy.deepcopy(group)
        later["id"] = "later"
        full.insert(3, later)
    with pytest.raises(ValidationError, match="duplicate"):
        validate(raw, tmp_path)


def test_ids_can_repeat_across_pipelines_and_terminal_names_are_allowed(raw, tmp_path):
    raw["pipelines"]["0-custom"] = copy.deepcopy(raw["pipelines"]["fast"])
    raw["pipelines"]["0-custom"][-1]["id"] = "approved"
    raw["pipelines"]["0-custom"][0]["id"] = "merged"
    raw["pipelines"]["0-custom"][-1]["onChanges"] = "merged"
    validate(raw, tmp_path)


@pytest.mark.parametrize("uses", ["", "Upper", "0-role", "../builder", "builder.md", "a" * 65])
def test_role_name_pattern(raw, tmp_path, uses):
    raw["pipelines"]["fast"][0]["uses"] = uses
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_role_must_resolve_for_selected_provider(raw, tmp_path):
    raw["pipelines"]["fast"][0]["uses"] = "custom"
    roles = tmp_path / "factory" / "roles"
    roles.mkdir(parents=True)
    (roles / "custom.codex.md").write_text("---\ntype: task\n---\nBuild.\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Paths tried") as exc:
        validate(raw, tmp_path)
    assert str(roles / "custom.claude.md") in str(exc.value)


def test_provider_must_be_known(raw, tmp_path):
    raw["pipelines"]["fast"][0]["provider"] = "other"
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


@pytest.mark.parametrize("selection", [
    None, {}, {
        "model": "opus"
    }, {
        "effort": "medium"
    }, {
        "model": "",
        "effort": "medium"
    }, {
        "model": "opus",
        "effort": " "
    }, {
        "model": [],
        "effort": "medium"
    }
])
def test_selected_profile_needs_explicit_model_and_effort(raw, tmp_path, selection):
    execution = raw["pipelines"]["fast"][0]
    if selection is None:
        del execution["profiles"]["claude"]
    else:
        execution["profiles"]["claude"] = selection
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_unselected_profile_is_optional_and_preserved(raw, tmp_path):
    selected = raw["pipelines"]["fast"][0]
    selected["profiles"]["codex"] = {"model": "future-codex-model", "effort": "high"}
    assert validate(raw,
                    tmp_path).pipelines["fast"][0].profiles["codex"].model == "future-codex-model"
    del selected["profiles"]["codex"]
    validate(raw, tmp_path)


def test_unknown_profile_provider_is_rejected(raw, tmp_path):
    raw["pipelines"]["fast"][0]["profiles"]["other"] = {"model": "x", "effort": "high"}
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_roles_can_repeat_inside_parallel_group(raw, tmp_path):
    group = raw["pipelines"]["full"][2]
    group["parallel"][1]["uses"] = group["parallel"][0]["uses"]
    group["parallel"][1]["provider"] = "codex"
    config = validate(raw, tmp_path)
    assert config.role_for("full", "security").type == "review"
    assert config.role_for("full", "ux").provider == "codex"
    # Repeating a role with the same provider is also valid.
    group["parallel"][1]["provider"] = "claude"
    validate(raw, tmp_path)


@pytest.mark.parametrize("case",
                         ["empty", "no-approval", "two-approvals", "approval-first", "too-many"])
def test_pipeline_ends_with_exactly_one_approval_and_at_most_32_steps(raw, tmp_path, case):
    full = raw["pipelines"]["full"]
    if case == "empty":
        full.clear()
    elif case == "no-approval":
        full.pop()
    elif case == "two-approvals":
        full.insert(0, {**copy.deepcopy(full[-1]), "id": "early"})
    elif case == "approval-first":
        full.append(full.pop(0))
    else:
        raw["pipelines"]["full"] = [{
            **copy.deepcopy(full[0]), "id": f"task-{i}"
        } for i in range(32)] + [full[-1]]
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_pipeline_accepts_32_steps_and_16_executions(raw, tmp_path):
    full = raw["pipelines"]["full"]
    group = full[2]
    group["parallel"] = [{
        **copy.deepcopy(group["parallel"][0]), "id": f"check-{i}"
    } for i in range(16)]
    full[2:2] = [{**copy.deepcopy(full[0]), "id": f"task-{i}"} for i in range(28)]
    validate(raw, tmp_path)


@pytest.mark.parametrize("count", [0, 17])
def test_parallel_group_has_one_to_16_executions(raw, tmp_path, count):
    group = raw["pipelines"]["full"][2]
    group["parallel"] = [{
        **copy.deepcopy(group["parallel"][0]), "id": f"check-{i}"
    } for i in range(count)]
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_parallel_groups_contain_reviews(raw, tmp_path):
    raw["pipelines"]["full"][2]["parallel"][0]["uses"] = "builder"
    with pytest.raises(ValidationError, match="only reviews"):
        validate(raw, tmp_path)


@pytest.mark.parametrize("field", ["uses", "profiles", "provider"])
def test_parallel_group_cannot_also_be_an_execution(raw, tmp_path, field):
    raw["pipelines"]["full"][2][field] = raw["pipelines"]["full"][0][field]
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


@pytest.mark.parametrize("index,target", [(2, "approve"), (2, "review"), (3, "review"),
                                          (2, "security"), (2, "missing"), (1, "spec"),
                                          (1, "build")])
def test_on_changes_requires_earlier_task_from_review_or_approval(raw, tmp_path, index, target):
    raw["pipelines"]["full"][index]["onChanges"] = target
    with pytest.raises(ValidationError, match="onChanges"):
        validate(raw, tmp_path)


def test_on_changes_is_valid_on_review_and_approval(raw, tmp_path):
    raw["pipelines"]["full"][-1]["onChanges"] = "build"
    validate(raw, tmp_path)


@pytest.mark.parametrize("limit", [0, 17, True, "4", 1.5])
def test_per_job_concurrency_range(raw, tmp_path, limit):
    raw["concurrency"]["perJob"] = limit
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


@pytest.mark.parametrize("limit", [1, 16])
def test_per_job_concurrency_boundaries(raw, tmp_path, limit):
    raw["concurrency"]["perJob"] = limit
    assert validate(raw, tmp_path).concurrency.perJob == limit


def test_timeout_override_is_optional_and_preserved(raw, tmp_path):
    raw["timeouts"]["executionMinutes"] = 30
    raw["pipelines"]["fast"][0]["timeoutMinutes"] = 5
    config = validate(raw, tmp_path)
    assert config.timeouts.executionMinutes == 30
    assert config.pipelines["fast"][0].timeoutMinutes == 5
    assert config.pipelines["fast"][1].timeoutMinutes is None


@pytest.mark.parametrize("value", [0, -1, float("inf"), "60", True])
def test_default_timeout_must_be_positive_finite_number(raw, tmp_path, value):
    raw["timeouts"]["executionMinutes"] = value
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


@pytest.mark.parametrize("field", ["timeoutMinutes", "maxBudgetUsd"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), "5", True])
def test_execution_limits_must_be_positive_finite_numbers(raw, tmp_path, field, value):
    raw["pipelines"]["fast"][0][field] = value
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_codex_budget_warns_and_claude_budget_is_preserved(raw, tmp_path):
    raw["pipelines"]["fast"][0]["maxBudgetUsd"] = 2.5
    raw["pipelines"]["fast"][1]["maxBudgetUsd"] = 4
    with pytest.warns(UserWarning, match="maxBudgetUsd is ignored for Codex"):
        config = validate(raw, tmp_path)
    assert config.pipelines["fast"][0].maxBudgetUsd == 2.5
    assert config.pipelines["fast"][1].maxBudgetUsd == 4


@pytest.mark.parametrize("field,value", [("version", 3),
                                         ("merge", "other"), ("permissions", "other"),
                                         ("maxRounds", 0), ("maxRounds", 6), ("engine", "")])
def test_schema_fields_are_validated(raw, tmp_path, field, value):
    raw[field] = value
    with pytest.raises(ValidationError):
        validate(raw, tmp_path)


def test_upgrade_current_v1_config_is_pure(tmp_path):
    raw = json.loads((ROOT / "factory" / "config.json").read_text(encoding="utf-8"))
    before = copy.deepcopy(raw)
    upgraded = upgrade_v1(raw, engine_version="1.2.3")
    assert raw == before
    assert upgraded["engine"] == ">=1.0,<2"
    assert upgraded["defaultPipeline"] == "full"
    assert set(upgraded["pipelines"]) == {"fast", "full"}
    assert not {"pipeline", "provider", "start", "headlessPermissions"} & upgraded.keys()
    assert upgraded["merge"] == raw["merge"]
    for step in upgraded["pipelines"]["full"]:
        for execution in step.get("parallel", [step]):
            assert execution["provider"] == raw["provider"]
            assert "providers" not in execution
            assert set(execution["profiles"]) == {"claude", "codex"}
    validate(upgraded, tmp_path)
    upgraded["pipelines"]["full"][0]["profiles"]["claude"]["model"] = "changed"
    assert raw == before


def test_upgrade_v1_fixture(tmp_path):
    raw = json.loads((ROOT / "tests/fixtures/v1/factory/config.json").read_text())
    before = copy.deepcopy(raw)
    upgraded = upgrade_v1(raw)
    config = validate_config(upgraded, root=tmp_path)
    assert raw == before
    assert config.defaultPipeline == "full"
    assert config.pipelines["full"][0].uses == "spec-writer"


@pytest.mark.parametrize("permissions,expected", [("auto", "auto"),
                                                  ("bypassPermissions", "bypass")])
def test_upgrade_omitted_pipeline_and_permissions(tmp_path, permissions, expected):
    upgraded = upgrade_v1({
        "provider": "codex",
        "headlessPermissions": permissions,
        "start": "session"
    })
    config = validate(upgraded, tmp_path)
    assert config.permissions == expected
    assert config.merge == "auto"
    assert [e.id for e in config.pipelines["full"][2].executions
            ] == ["reviewer-security", "reviewer-ux", "reviewer-ui", "reviewer-code"]
    assert all(e.provider == "codex" for s in config.pipelines["full"] for e in s.executions)


def test_empty_v1_config_preserves_defaults_and_current_major(tmp_path):
    config = validate_config({}, root=tmp_path)
    major = int(__version__.split(".", 1)[0])
    assert config.engine == f">={major}.0,<{major + 1}"
    assert config.merge == "auto"
    assert config.permissions == "auto" and config.maxRounds == 3
    assert all(e.provider == "claude" for s in config.pipelines["full"] for e in s.executions)


@pytest.mark.parametrize("version,expected", [("1.4.2", ">=1.0,<2"), ("2.1.0", ">=2.0,<3"),
                                              ("1.0.0rc1", ">=1.0.0rc1,<2"),
                                              ("2.0.0b2", ">=2.0.0b2,<3")])
def test_engine_range_always_covers_its_source_version(version, expected):
    assert engine_range(version) == expected
    assert versioning.matches(version, engine_range(version))


def test_engine_range_defaults_to_running_version():
    assert engine_range() == engine_range(__version__)


def test_upgrade_v2_is_idempotent_and_does_not_alias(raw):
    upgraded = upgrade_v1(raw)
    assert upgraded == raw and upgraded is not raw
    assert upgrade_v1(upgraded) == upgraded
    upgraded["pipelines"]["fast"].clear()
    assert len(raw["pipelines"]["fast"]) == 2


@pytest.mark.parametrize("version", [0, 3, "1", True])
def test_upgrade_rejects_unknown_versions(version):
    with pytest.raises(ValueError, match="version"):
        upgrade_v1({"version": version})


def test_loading_upgrades_v1_in_memory_only(tmp_path):
    directory = tmp_path / "factory"
    directory.mkdir()
    path = directory / "config.json"
    original = '{"provider":"codex","merge":"auto"}'
    path.write_text(original, encoding="utf-8-sig")
    config = load_config(tmp_path)
    assert config.version == 2 and config.merge == "auto"
    assert path.read_text(encoding="utf-8-sig") == original


def test_machine_settings_defaults_and_persistence_respect_factory_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    connection = open_registry()
    try:
        defaults = load_machine_settings(connection)
        assert defaults.concurrency.global_ == 6
        assert defaults.ports.range == "5100-5999"
        assert defaults.cli.claude is None and defaults.cli.codex is None
        settings = MachineSettings.model_validate({
            "concurrency": {
                "global": 32
            },
            "cli": {
                "codex": str(tmp_path / "CLI with spaces" / "codex.exe")
            },
            "ports": {
                "range": "6000-6999"
            }
        })
        with db.tx(connection):
            connection.execute("INSERT INTO settings VALUES ('future.key', 'kept')")
        assert save_machine_settings(connection, settings) == settings
    finally:
        connection.close()
    connection = open_registry()
    try:
        assert load_machine_settings(connection) == settings
        rows = dict(connection.execute("SELECT key, value FROM settings"))
        assert rows["future.key"] == "kept"
        assert json.loads(rows["concurrency.global"]) == 32
    finally:
        connection.close()
    assert (tmp_path / "machine" / "machine.db").is_file()


@pytest.mark.parametrize("limit", [0, 33, True, "6", 6.5])
def test_machine_concurrency_is_one_to_32(limit):
    with pytest.raises(ValidationError):
        MachineSettings.model_validate({"concurrency": {"global": limit}})


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_machine_cli_path_must_be_absolute(provider):
    with pytest.raises(ValidationError, match="absolute"):
        MachineSettings.model_validate({"cli": {provider: "relative/cli.exe"}})


@pytest.mark.parametrize("ports", ["invalid", "0-5999", "5999-5100", "1-65536", "-1-6000", "1-2-3"])
def test_machine_port_range_is_validated(ports):
    with pytest.raises(ValidationError):
        MachineSettings.model_validate({"ports": {"range": ports}})


def test_invalid_machine_settings_do_not_write(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
    connection = open_registry()
    try:
        with pytest.raises(ValidationError):
            save_machine_settings(connection, {"concurrency": {"global": 0}})
        assert connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
        assert not connection.in_transaction
    finally:
        connection.close()


def test_machine_settings_write_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
    connection = open_registry()
    try:
        with db.tx(connection):
            connection.execute(
                "CREATE TRIGGER reject_cli BEFORE INSERT ON settings "
                "WHEN NEW.key = 'cli.codex' BEGIN SELECT RAISE(ABORT, 'rejected'); END")
        with pytest.raises(sqlite3.IntegrityError, match="rejected"):
            save_machine_settings(connection, MachineSettings())
        assert connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
        assert not connection.in_transaction
    finally:
        connection.close()
