"""Model and role lists the editor offers, read from the installed tools."""

import json
import subprocess

import pytest

from agent_factory.engine import catalog, roles
from test_engine_api import api, headers, repo  # noqa: F401  (fixtures)

CODEX = json.dumps({
    "models": [
        {
            "slug": "gpt-a",
            "visibility": "list",
            "default_reasoning_level": "low",
            "supported_reasoning_levels": [{
                "effort": "low"
            }, {
                "effort": "ultra"
            }]
        },
        {
            "slug": "gpt-hidden",
            "visibility": "hide",
            "supported_reasoning_levels": [{
                "effort": "low"
            }]
        },
        {
            "slug": "gpt-b",
            "visibility": "list",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [{
                "effort": "low"
            }, {
                "effort": "medium"
            }]
        },
    ]
})
OPENCODE = "Loading models...\nopenai/gpt-x\nopencode/free-model\nopenai/gpt-x\n\n"


@pytest.fixture(autouse=True)
def fresh_cache():
    catalog._cache.clear()
    yield
    catalog._cache.clear()


def tools(tmp_path):
    paths = {}
    for name in ("codex", "opencode"):
        paths[name] = str(tmp_path / f"{name}.exe")
        (tmp_path / f"{name}.exe").write_text("")
    return paths


def runner(calls, outputs=None):
    outputs = outputs or {"debug": CODEX, "models": OPENCODE}

    def run(command, timeout):
        calls.append(command[1])
        return outputs[command[1]]

    return run


def test_codex_catalog_hides_hidden_models_and_keeps_each_models_efforts():
    parsed = catalog.parse_codex(CODEX)
    assert [m["id"] for m in parsed["models"]] == ["gpt-a", "gpt-b"]
    assert parsed["models"][0] == {
        "id": "gpt-a",
        "efforts": ["low", "ultra"],
        "defaultEffort": "low"
    }
    assert parsed["efforts"] == ["low", "ultra", "medium"]
    with pytest.raises(ValueError):
        catalog.parse_codex(json.dumps({"models": []}))


def test_opencode_models_are_provider_slash_model_lines_without_noise_or_duplicates():
    parsed = catalog.parse_opencode(OPENCODE)
    assert [m["id"] for m in parsed["models"]] == ["openai/gpt-x", "opencode/free-model"]
    with pytest.raises(ValueError):
        catalog.parse_opencode("nothing useful\n")


def test_discover_reads_each_tool_once_then_serves_the_cache_until_refreshed(tmp_path):
    calls = []
    now = [0.0]
    args = dict(run=runner(calls), clock=lambda: now[0])
    first = catalog.discover(tools(tmp_path), **args)
    assert first["codex"]["source"] == "cli" and first["opencode"]["source"] == "cli"
    assert first["claude"]["source"] == "builtin" and first["claude"]["models"][0]["id"] == "opus"
    assert sorted(calls) == ["debug", "models"]
    catalog.discover(tools(tmp_path), **args)
    assert len(calls) == 2  # cached
    catalog.discover(tools(tmp_path), refresh=True, **args)
    assert len(calls) == 4
    now[0] = catalog.CACHE_SECONDS + 1
    catalog.discover(tools(tmp_path), **args)
    assert len(calls) == 6  # expired


def test_a_failing_or_slow_tool_falls_back_with_a_reason_and_is_retried(tmp_path):

    def broken(command, timeout):
        if command[1] == "debug":
            raise subprocess.TimeoutExpired(command, timeout)
        raise RuntimeError("server did not start")

    result = catalog.discover(tools(tmp_path), run=broken)
    assert result["codex"]["source"] == "builtin" and result["codex"]["models"] == []
    assert "did not answer" in result["codex"]["detail"]
    assert "server did not start" in result["opencode"]["detail"]
    assert result["codex"]["efforts"]  # the dropdown still has effort choices
    calls = []
    again = catalog.discover(tools(tmp_path), run=runner(calls))
    assert again["codex"]["source"] == "cli" and len(calls) == 2  # failures are not cached


def test_a_tool_that_is_not_installed_is_reported_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog.shutil, "which", lambda name: None)
    calls = []
    result = catalog.discover({}, run=runner(calls))
    assert calls == []
    assert result["codex"]["detail"] == "codex is not installed."
    assert result["opencode"]["source"] == "builtin"


def test_available_roles_lists_builtins_and_the_repositorys_own_per_provider(tmp_path):
    folder = tmp_path / "factory" / "roles"
    folder.mkdir(parents=True)
    (folder / "docs-writer.md").write_text(
        "---\ntype: task\ndescription: Write docs.\n---\nWrite the docs.\n", encoding="utf-8")
    (folder / "builder.claude.md").write_text(
        "---\ntype: task\ndescription: Claude builder.\n---\nBuild it.\n", encoding="utf-8")
    (folder / "broken.md").write_text("no frontmatter", encoding="utf-8")
    listed = roles.available_roles(tmp_path)
    assert set(listed) == {"claude", "codex", "opencode"}
    names = [r["name"] for r in listed["claude"]]
    assert names.count("builder") == 1 and "docs-writer" in names and "broken" not in names
    assert [r["type"] for r in listed["claude"]] == sorted((r["type"] for r in listed["claude"]),
                                                           key=["task", "review", "approval"].index)
    custom = {r["name"]: r["custom"] for r in listed["claude"]}
    assert custom["docs-writer"] is True and custom["builder"] is True  # claude override
    assert {r["name"]: r["custom"] for r in listed["codex"]}["builder"] is False


def test_api_serves_available_roles_and_provider_models(api, monkeypatch):  # noqa: F811
    client, _, repo_id, _ = api
    response = client.get(f"/api/repos/{repo_id}/roles/available", headers=headers())
    assert response.status_code == 200
    assert "builder" in [r["name"] for r in response.json()["roles"]["codex"]]
    seen = {}

    def fake(paths, *, refresh=False):
        seen.update(paths=paths, refresh=refresh)
        return {"codex": {"source": "cli", "models": [], "efforts": [], "detail": None}}

    monkeypatch.setattr(catalog, "discover", fake)
    body = client.get("/api/providers/models?refresh=true", headers=headers()).json()
    assert body["providers"]["codex"]["source"] == "cli"
    assert seen["refresh"] is True and set(seen["paths"]) == {"claude", "codex", "opencode"}
