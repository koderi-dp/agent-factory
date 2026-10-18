"""The machine-wide pipeline library."""

import json

import pytest

from agent_factory.engine import library
from agent_factory.engine.config import template_config
from agent_factory.paths import machine_dir
from test_engine_api import api, headers, repo  # noqa: F401  (fixtures)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    return tmp_path


def test_builtins_are_listed_first_and_cannot_be_overwritten_or_deleted(home):
    names = [(p["name"], p["builtin"]) for p in library.list_pipelines()]
    assert names[:2] == [("fast", True), ("full", True)] or names[:2] == [("full", True),
                                                                          ("fast", True)]
    with pytest.raises(ValueError, match="built-in"):
        library.save("full", template_config()["pipelines"]["full"])
    with pytest.raises(ValueError, match="built-in"):
        library.delete("fast")


def test_save_validates_persists_and_deletes(home):
    steps = template_config()["pipelines"]["fast"]
    library.save("quick", steps)
    stored = json.loads((machine_dir() / "pipelines.json").read_text(encoding="utf-8"))
    assert stored == {"quick": steps}
    assert {"name": "quick", "steps": steps, "builtin": False} in library.list_pipelines()
    library.delete("quick")
    assert "quick" not in [p["name"] for p in library.list_pipelines()]
    with pytest.raises(KeyError):
        library.delete("quick")


def test_save_rejects_bad_names_and_roles_that_only_one_repo_has(home):
    steps = template_config()["pipelines"]["fast"]
    with pytest.raises(ValueError, match="lowercase"):
        library.save("Bad Name", steps)
    custom = json.loads(json.dumps(steps))
    custom[0]["uses"] = "only-in-one-repo"
    with pytest.raises(ValueError):
        library.save("custom", custom)
    assert not (machine_dir() / "pipelines.json").exists()


def test_api_lists_saves_and_deletes(api):  # noqa: F811
    client, _, _, _ = api
    steps = template_config()["pipelines"]["fast"]
    saved = client.put("/api/library/pipelines/quick", json={"steps": steps}, headers=headers())
    assert saved.status_code == 200 and saved.json()["name"] == "quick"
    bad = client.put("/api/library/pipelines/full", json={"steps": steps}, headers=headers())
    assert bad.status_code >= 400
    listed = client.get("/api/library/pipelines", headers=headers()).json()["pipelines"]
    assert "quick" in [p["name"] for p in listed]
    assert client.delete("/api/library/pipelines/quick", headers=headers()).status_code == 200
    assert client.delete("/api/library/pipelines/quick", headers=headers()).status_code == 404
