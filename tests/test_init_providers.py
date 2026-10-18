"""`factory init` picks its starting tool from what is installed instead of asking blindly."""

import json
import subprocess

import pytest

from agent_factory import cli
from agent_factory.engine import scaffold
from agent_factory.engine.scaffold import Tool
from test_engine_api import repo  # noqa: F401  (fixture)


def tools(**states):
    """states: name -> "ready" | "unconfirmed" | "missing"; unnamed tools are missing."""
    return {
        name:
        Tool(name,
             installed=states.get(name, "missing") != "missing",
             signed_in=states.get(name, "missing") == "ready")
        for name in scaffold.PROVIDERS
    }


def test_nothing_installed_stops_with_how_to_fix_it():
    choice = scaffold.choose_provider(tools())
    assert choice.provider is None and choice.skills == ()
    assert "Install Claude Code, Codex or OpenCode" in choice.note


def test_one_signed_in_tool_needs_no_question():
    choice = scaffold.choose_provider(tools(claude="ready"))
    assert (choice.provider, choice.options, choice.skills) == ("claude", (), ("claude", ))
    assert "only signed-in tool" in choice.note


def test_a_signed_in_tool_wins_over_one_that_is_only_installed_but_both_get_shortcuts():
    choice = scaffold.choose_provider(tools(claude="unconfirmed", codex="ready"))
    assert (choice.provider, choice.options) == ("codex", ())
    assert choice.skills == ("claude", "codex")


def test_unconfirmed_sign_in_warns_but_does_not_block():
    choice = scaffold.choose_provider(tools(opencode="unconfirmed"))
    assert choice.provider == "opencode"
    assert "Could not confirm you are signed in to OpenCode" in choice.note


def test_several_tools_ask_and_a_mix_is_offered_for_any_combination():
    cases = {
        ("claude", "codex"): ("claude", "codex", "both"),
        ("claude", "opencode"): ("claude", "opencode", "both"),
        ("codex", "opencode"): ("codex", "opencode", "both"),
        ("claude", "codex", "opencode"): ("claude", "codex", "opencode", "both"),
    }
    for ready, expected in cases.items():
        choice = scaffold.choose_provider(tools(**{name: "ready" for name in ready}))
        assert choice.provider == ready[0] and choice.options == expected
        assert choice.available == ready


def test_detect_tools_uses_overrides_and_saved_credentials(tmp_path, monkeypatch):
    present = tmp_path / "claude.exe"
    present.write_text("")
    monkeypatch.setattr(scaffold.shutil, "which", lambda name: None)
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    monkeypatch.setattr(scaffold, "credential_files", lambda name: [creds]
                        if name == "claude" else [tmp_path / "none"])
    found = scaffold.detect_tools({"claude": str(present), "codex": str(tmp_path / "gone")})
    assert found["claude"] == Tool("claude", installed=True, signed_in=True)
    assert found["codex"] == Tool("codex", installed=False, signed_in=False)
    assert found["opencode"].installed is False


def test_init_installs_shortcuts_for_exactly_the_tools_it_is_given(repo):  # noqa: F811
    messages = scaffold.init(repo, "claude", skills=("claude", "codex"))
    assert "wrote: .claude/skills/factory/SKILL.md" in messages
    assert "wrote: .agents/skills/factory/SKILL.md" in messages
    assert not (repo / ".opencode").exists()
    config = json.loads((repo / "factory/config.json").read_text(encoding="utf-8"))
    providers = {
        e["provider"]
        for steps in config["pipelines"].values()
        for s in steps
        for e in s.get("parallel", [s])
    }
    assert providers == {"claude"}


def stage_providers(repo):
    config = json.loads((repo / "factory/config.json").read_text(encoding="utf-8"))
    return [(name, e["id"], e["provider"]) for name, steps in config["pipelines"].items()
            for s in steps for e in s.get("parallel", [s])]


def test_a_mix_keeps_the_templates_tool_per_stage_when_it_is_available(repo):  # noqa: F811
    from agent_factory.engine.config import template_config
    scaffold.init(repo, "both", available=("claude", "codex", "opencode"))
    template = template_config()
    expected = [(name, e["id"], e["provider"]) for name, steps in template["pipelines"].items()
                for s in steps for e in s.get("parallel", [s])]
    assert stage_providers(repo) == expected


def test_a_mix_moves_a_stage_to_the_first_available_tool_when_its_own_is_missing(
        repo):  # noqa: F811
    scaffold.init(repo, "both", available=("opencode", "claude"))
    providers = {provider for _, _, provider in stage_providers(repo)}
    assert providers == {"opencode", "claude"}  # codex stages moved to opencode, claude kept


def run_init(repo, monkeypatch, found, *extra):  # noqa: F811
    monkeypatch.setattr(scaffold, "detect_tools", lambda overrides=None: found)
    return cli.main(["--repo", str(repo), "init", *extra])


def test_cli_uses_the_only_tool_without_asking(repo, monkeypatch, capsys):  # noqa: F811
    assert run_init(repo, monkeypatch, tools(claude="ready"), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Using Claude Code for every stage" in out
    assert "would write: .claude/skills/factory/SKILL.md" in out
    assert not (repo / "factory").exists()


def test_cli_without_a_terminal_starts_on_the_first_tool_and_says_so(repo, monkeypatch,
                                                                     capsys):  # noqa: F811
    found = tools(claude="ready", codex="ready")
    assert run_init(repo, monkeypatch, found, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "No terminal: starting the pipeline on Claude Code." in out
    assert "would write: .agents/skills/factory/SKILL.md" in out


def test_cli_stops_before_writing_when_no_tool_is_installed(repo, monkeypatch,
                                                            capsys):  # noqa: F811
    assert run_init(repo, monkeypatch, tools()) == 1
    assert "Install Claude Code, Codex or OpenCode" in capsys.readouterr().err
    assert not (repo / "factory").exists()


def test_cli_menu_lets_the_user_pick_by_number(repo, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y" if "Continue" in prompt else "2")
    assert run_init(repo, monkeypatch, tools(claude="ready", codex="ready")) == 0
    out = capsys.readouterr().out
    assert "1) Claude Code" in out and "2) Codex" in out and "3) Mixed" in out
    config = json.loads((repo / "factory/config.json").read_text(encoding="utf-8"))
    providers = {
        e["provider"]
        for steps in config["pipelines"].values()
        for s in steps
        for e in s.get("parallel", [s])
    }
    assert providers == {"codex"}
    assert (repo / ".claude/skills/factory/SKILL.md").is_file()


def test_cli_menu_rejects_an_answer_outside_the_list(repo, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "9")
    assert run_init(repo, monkeypatch, tools(claude="ready", codex="ready")) == 1
    assert "Pick a number from 1 to 3." in capsys.readouterr().err
    assert not (repo / "factory").exists()


def test_an_explicit_provider_skips_detection(repo, monkeypatch, capsys):  # noqa: F811

    def boom(overrides=None):
        raise AssertionError("detection must not run with --provider")

    monkeypatch.setattr(scaffold, "detect_tools", boom)
    assert cli.main(["--repo", str(repo), "init", "--provider", "codex", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would write: .agents/skills/factory/SKILL.md" in out
    assert ".claude/skills" not in out


def test_starter_files_follow_the_tools_in_use(tmp_path):
    only_claude = scaffold.starter_docs(tmp_path, {"claude"})
    assert [name for name, _ in only_claude] == ["CLAUDE.md"]
    assert [name for name, _ in scaffold.starter_docs(tmp_path, {"codex"})] == ["AGENTS.md"]
    for tools in ({"claude", "codex"}, {"claude", "opencode"}, set(scaffold.PROVIDERS)):
        docs = scaffold.starter_docs(tmp_path, tools)
        assert [name for name, _ in docs] == ["AGENTS.md", "CLAUDE.md"]
        assert docs[1][1] == scaffold.CLAUDE_IMPORT and "@AGENTS.md" in docs[1][1]
        assert docs[0][1] == only_claude[0][1]  # one set of instructions, not two


def test_a_fresh_folder_shared_by_tools_commits_both_files(tmp_path, monkeypatch):
    identity = tmp_path / "gitconfig"
    identity.write_text("[user]\n\tname = test\n\temail = test@localhost\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(identity))
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "fresh"
    root.mkdir()
    plan = scaffold.init(root, "both", dry_run=True, skills=("claude", "codex"))
    assert "would write: CLAUDE.md (imports AGENTS.md for Claude Code)" in plan
    assert "would commit: AGENTS.md, CLAUDE.md as the first commit on main" in plan
    scaffold.init(root, "both", skills=("claude", "codex"))
    committed = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                               cwd=root,
                               capture_output=True,
                               text=True,
                               check=True).stdout.split()
    assert sorted(committed) == ["AGENTS.md", "CLAUDE.md"]
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_an_existing_instructions_file_is_left_alone(repo):  # noqa: F811
    # The fixture repo already has AGENTS.md; init must not add a CLAUDE.md next to it.
    scaffold.init(repo, "both", skills=("claude", "codex"))
    assert not (repo / "CLAUDE.md").exists()
