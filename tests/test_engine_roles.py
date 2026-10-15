"""Role precedence, provider isolation and YAML parsing (plan §5.3)."""

import hashlib
from pathlib import Path

import pytest

from agent_factory import __version__
from agent_factory.engine.roles import BUILTIN_DIR, RoleError, resolve_role

ROOT = Path(__file__).resolve().parents[1]


def write_role(path, text="---\ntype: task\n---\nInstructions.\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_resolution_order_and_freezing_evidence(tmp_path, provider):
    repo = tmp_path / "repo"
    builtins = tmp_path / "builtins"
    paths = [repo / "factory" / "roles" / f"custom.{provider}.md",
             repo / "factory" / "roles" / "custom.md",
             builtins / f"custom.{provider}.md", builtins / "custom.md"]
    for i, path in enumerate(paths):
        write_role(path, f"---\ntype: task\ndescription: Role {i}\n---\nInstructions {i}.\n")
    for i, path in enumerate(paths):
        role = resolve_role("custom", provider, root=repo, builtin_dir=builtins)
        assert role.path == path
        assert role.description == f"Role {i}"
        assert role.text == path.read_bytes().decode("utf-8")
        assert role.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert role.source == (str(path) if i < 2 else f"built-in@{__version__}")
        path.unlink()
        # The resolved role is an immutable snapshot, independent of subsequent edits.
        assert f"Instructions {i}." in role.instructions


@pytest.mark.parametrize("provider,other", [("claude", "codex"), ("codex", "claude")])
def test_no_cross_provider_fallback_and_error_lists_all_paths(tmp_path, provider, other):
    builtins = tmp_path / "builtins"
    roles = tmp_path / "factory" / "roles"
    write_role(roles / f"custom.{other}.md")
    write_role(builtins / f"custom.{other}.md")
    with pytest.raises(RoleError) as exc:
        resolve_role("custom", provider, root=tmp_path, builtin_dir=builtins)
    tried = str(exc.value).split("Paths tried:\n")[1].splitlines()
    assert tried == [str(roles / f"custom.{provider}.md"), str(roles / "custom.md"),
                     str(builtins / f"custom.{provider}.md"), str(builtins / "custom.md")]


def test_invalid_override_does_not_fall_back(tmp_path):
    write_role(tmp_path / "factory" / "roles" / "builder.codex.md", "No frontmatter")
    with pytest.raises(RoleError, match="builder.codex.md"):
        resolve_role("builder", "codex", root=tmp_path)


def test_yaml_safe_load_supports_quoted_and_multiline_metadata(tmp_path):
    path = tmp_path / "factory" / "roles" / "custom.md"
    write_role(path, "---\ntype: 'task' # comment\ncontract: spec\ndescription: |\n"
                     "  Two lines\n  of description.\noutput: ../ignored.md\n"
                     "ejectedFrom: custom@1.0.0#abcd\n---\n\n# Instructions\n")
    role = resolve_role("custom", "codex", root=tmp_path)
    assert role.contract == "spec"
    assert role.description == "Two lines\nof description.\n"
    assert not hasattr(role, "output")


@pytest.mark.parametrize("text", [
    "No frontmatter", "---\ntype: task", "---\n- task\n---\nInstructions",
    "---\ndescription: no type\n---\nInstructions", "---\ntype: unknown\n---\nInstructions",
    "---\ntype: task\ntype: review\n---\nInstructions",
    "---\ntype: review\ncontract: spec\n---\nInstructions",
    "---\ntype: task\ncontract: decision\n---\nInstructions", "---\ntype: task\n---\n \n",
    "---\ntype: [task\n---\nInstructions",
    "---\n!!python/object/apply:os.system ['echo unsafe']\n---\nInstructions",
])
def test_invalid_frontmatter_and_empty_instructions(tmp_path, text):
    path = tmp_path / "factory" / "roles" / "custom.md"
    write_role(path, text)
    with pytest.raises(RoleError, match="custom.md"):
        resolve_role("custom", "claude", root=tmp_path)


@pytest.mark.parametrize("name", ["../builder", "builder.md", "C:/builder", "A", "0-role", "a" * 65])
def test_unsafe_role_names_are_rejected(tmp_path, name):
    with pytest.raises(RoleError, match="name"):
        resolve_role(name, "codex", root=tmp_path)


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(RoleError, match="provider"):
        resolve_role("builder", "other", root=tmp_path)


def test_v1_role_texts_are_preserved_byte_for_byte(tmp_path):
    for source in (ROOT / "claude" / "agents").glob("*.md"):
        codex = ROOT / "codex" / "agents" / source.name
        assert (BUILTIN_DIR / source.name).read_bytes() == source.read_bytes()
        assert (BUILTIN_DIR / f"{source.stem}.codex.md").read_bytes() == codex.read_bytes()
        for provider in ("claude", "codex"):
            role = resolve_role(source.stem, provider, root=tmp_path)
            expected = codex if provider == "codex" else source
            assert role.sha256 == hashlib.sha256(expected.read_bytes()).hexdigest()


def test_fast_approver_has_interim_approval_contract(tmp_path):
    # TODO (plan §10.2, session 2.1): Fast-specific text replaces this interim copy.
    for provider in ("claude", "codex"):
        fast = resolve_role("fast-approver", provider, root=tmp_path)
        approver = resolve_role("approver", provider, root=tmp_path)
        assert fast.type == "approval" and fast.contract == "decision"
        assert fast.text == approver.text
