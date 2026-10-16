"""Machine paths, stable registration, and registry command behaviour."""

import hashlib
import os
import pytest

from agent_factory import __version__, cli, paths
from agent_factory.engine import db, registry


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "machine"
    monkeypatch.setenv("FACTORY_HOME", str(home))
    connection = registry.open_registry()
    yield connection
    connection.close()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "My App"
    (repo / "factory").mkdir(parents=True)
    (repo / "factory" / "config.json").write_text("{}", encoding="utf-8")
    return repo


def test_factory_home(machine, tmp_path, monkeypatch):
    assert paths.machine_dir() == tmp_path / "machine"
    assert (paths.machine_dir() / "machine.db").is_file()
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "other"))
    assert paths.machine_dir() == tmp_path / "other"


def test_default_machine_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    monkeypatch.setattr(paths, "user_data_path", lambda *args, **kwargs: tmp_path / "platform")
    assert paths.machine_dir() == tmp_path / "platform"


def test_find_repo_walks_and_prefers_nearest(repo, tmp_path):
    child = repo / "nested" / "deep"
    child.mkdir(parents=True)
    assert paths.find_repo(child) == paths.normalize_repo_path(repo)
    assert paths.find_repo(repo / "factory" / "config.json") == paths.normalize_repo_path(repo)
    assert paths.find_repo(tmp_path) is None
    (child / "factory").mkdir()
    (child / "factory" / "config.json").write_text("{}", encoding="utf-8")
    assert paths.find_repo(child) == paths.normalize_repo_path(child)


def test_registration_stable_and_preserves_name(machine, repo):
    first = registry.register(machine, repo)
    assert first.name == repo.name
    registry.rename(machine, first.id, "Friendly name")
    again = registry.register(machine, repo / "factory" / "..")
    assert again.id == first.id
    assert again.added_at == first.added_at
    assert again.name == "Friendly name"
    digest = hashlib.sha256(str(paths.normalize_repo_path(repo)).encode("utf-8")).hexdigest()[:6]
    assert first.id == f"my-app-{digest}"
    assert len(registry.list_repos(machine)) == 1
    if os.name == "nt":
        assert registry.register(machine, str(repo).upper()).id == first.id


def test_distinct_paths_and_missing_restore(machine, repo, tmp_path):
    first = registry.register(machine, repo)
    other = tmp_path / "elsewhere" / repo.name
    (other / "factory").mkdir(parents=True)
    (other / "factory" / "config.json").write_text("{}", encoding="utf-8")
    assert registry.register(machine, other).id != first.id
    registry.mark_missing(machine, first.id)
    assert machine.execute("SELECT last_seen FROM repos WHERE id=?",
                           (first.id, )).fetchone()[0] is None
    moved = repo.rename(repo.with_name("moved"))
    assert next(item for item in registry.list_repos(machine) if item.id == first.id).missing
    moved.rename(repo)
    assert not any(item.missing for item in registry.list_repos(machine))


def test_remove_cascades_leases_and_unknown_ids_fail(machine, repo):
    item = registry.register(machine, repo)
    with db.tx(machine):
        machine.execute("INSERT INTO port_leases VALUES (5100, ?, 'job', 'now')", (item.id, ))
    registry.remove(machine, item.id)
    assert registry.list_repos(machine) == []
    assert machine.execute("SELECT * FROM port_leases").fetchall() == []
    for action in (registry.remove, registry.mark_missing):
        with pytest.raises(KeyError):
            action(machine, item.id)
    with pytest.raises(KeyError):
        registry.rename(machine, item.id, "new")
    with pytest.raises(ValueError):
        registry.register(machine, repo.parent)


def test_cli_version_and_registry(machine, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert cli.main(["repos"]) == 0
    item = registry.list_repos(machine)[0]
    assert item.id in capsys.readouterr().out
    assert cli.main(["repos", "rename", item.id, "Renamed"]) == 0
    assert registry.list_repos(machine)[0].name == "Renamed"
    assert cli.main(["repos", "remove", item.id]) == 0
    assert registry.list_repos(machine) == []
    assert cli.main(["repos", "remove", "absent"]) == 1
    assert "Unknown repo" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["init", "--provider", "both", "--dry-run"],
    ["upgrade", "--dry-run"],
    ["eject", "builder", "--diff"],
    ["doctor"],
    ["start", "title", "--wait"],
    ["next", "--pipeline", "fast"],
    ["status", "--json"],
    ["stop", "job"],
    ["resume", "job"],
    ["approve", "job", "--confirm-sensitive"],
    ["rework", "job", "note"],
    ["delete", "job", "--force"],
    ["dashboard"],
    ["serve", "--port", "5099"],
    ["service", "status"],
    ["service", "stop", "--stop-running"],
    ["service", "restart"],
    ["config", "--machine", "concurrency.global=6"],
])
def test_commands_are_registered(argv):
    assert cli.build_parser().parse_args(argv).command == argv[0]
