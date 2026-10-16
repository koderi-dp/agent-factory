"""Role precedence, provider isolation and YAML parsing (plan §5.3)."""

import hashlib
from pathlib import Path

import pytest

from agent_factory import __version__
from agent_factory.engine.reports import extract_sections
from agent_factory.engine.roles import BUILTIN_DIR, RoleError, resolve_role

BUILTINS = {
    "spec-writer": ("task", "spec", {"Acceptance criteria", "Pages to check"}),
    "builder": ("task", "status", {"Changes", "Decisions", "Acceptance", "Checks run"}),
    "reviewer-code": ("review", "verdict", {"Findings", "Checks run"}),
    "reviewer-security": ("review", "verdict", {"Findings", "Checks run"}),
    "reviewer-ui": ("review", "verdict", {"Findings", "Checks run"}),
    "reviewer-ux": ("review", "verdict", {"Findings", "Checks run"}),
    "approver": ("approval", "decision", {"Reasons", "Follow-ups"}),
    "fast-approver": ("approval", "decision", {"Reasons", "Follow-ups"}),
}


def write_role(path, text="---\ntype: task\n---\nInstructions.\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_resolution_order_and_freezing_evidence(tmp_path, provider):
    repo = tmp_path / "repo"
    builtins = tmp_path / "builtins"
    paths = [
        repo / "factory" / "roles" / f"custom.{provider}.md",
        repo / "factory" / "roles" / "custom.md", builtins / f"custom.{provider}.md",
        builtins / "custom.md"
    ]
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
    assert tried == [
        str(roles / f"custom.{provider}.md"),
        str(roles / "custom.md"),
        str(builtins / f"custom.{provider}.md"),
        str(builtins / "custom.md")
    ]


def test_invalid_override_does_not_fall_back(tmp_path):
    write_role(tmp_path / "factory" / "roles" / "builder.codex.md", "No frontmatter")
    with pytest.raises(RoleError, match="builder.codex.md"):
        resolve_role("builder", "codex", root=tmp_path)


def test_yaml_safe_load_supports_quoted_and_multiline_metadata(tmp_path):
    path = tmp_path / "factory" / "roles" / "custom.md"
    write_role(
        path, "---\ntype: 'task' # comment\ncontract: spec\ndescription: |\n"
        "  Two lines\n  of description.\noutput: ../ignored.md\n"
        "ejectedFrom: custom@1.0.0#abcd\n---\n\n# Instructions\n")
    role = resolve_role("custom", "codex", root=tmp_path)
    assert role.contract == "spec"
    assert role.description == "Two lines\nof description.\n"
    assert not hasattr(role, "output")


@pytest.mark.parametrize("text", [
    "No frontmatter",
    "---\ntype: task",
    "---\n- task\n---\nInstructions",
    "---\ndescription: no type\n---\nInstructions",
    "---\ntype: unknown\n---\nInstructions",
    "---\ntype: task\ntype: review\n---\nInstructions",
    "---\ntype: review\ncontract: spec\n---\nInstructions",
    "---\ntype: task\ncontract: decision\n---\nInstructions",
    "---\ntype: task\n---\n \n",
    "---\ntype: [task\n---\nInstructions",
    "---\n!!python/object/apply:os.system ['echo unsafe']\n---\nInstructions",
])
def test_invalid_frontmatter_and_empty_instructions(tmp_path, text):
    path = tmp_path / "factory" / "roles" / "custom.md"
    write_role(path, text)
    with pytest.raises(RoleError, match="custom.md"):
        resolve_role("custom", "claude", root=tmp_path)


@pytest.mark.parametrize("name",
                         ["../builder", "builder.md", "C:/builder", "A", "0-role", "a" * 65])
def test_unsafe_role_names_are_rejected(tmp_path, name):
    with pytest.raises(RoleError, match="name"):
        resolve_role(name, "codex", root=tmp_path)


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(RoleError, match="provider"):
        resolve_role("builder", "other", root=tmp_path)


@pytest.mark.parametrize("path", sorted(BUILTIN_DIR.glob("*.md")), ids=lambda p: p.name)
def test_every_builtin_file_fits_byte_budget_and_has_report_sections(path):
    if path.name == "README.md":
        return
    name = path.name.split(".")[0]
    assert name in BUILTINS
    # Decimal KB is the stricter interpretation; include metadata and UTF-8 bytes.
    assert len(path.read_bytes()) <= (6000 if name == "builder" else 4000)
    sections = extract_sections(path.read_text(encoding="utf-8"))
    assert BUILTINS[name][2] <= sections.keys()


@pytest.mark.parametrize("name", BUILTINS)
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_builtin_contract_and_packet_handoff(tmp_path, name, provider):
    role = resolve_role(name, provider, root=tmp_path)
    assert (role.type, role.contract) == BUILTINS[name][:2]
    assert role.path == BUILTIN_DIR / f"{name}.md"
    assert "Start from the execution packet." in role.instructions
    assert "Do not re-read earlier rounds' reports unless the packet points you to them." in role.instructions
    assert "report_path" in role.instructions
    for obsolete in ("job.json", "round-<N>", "decision.md", "build.md", "spec.md",
                     "Generated by tools/sync_codex.py"):
        assert obsolete not in role.instructions
    if role.type == "review":
        assert "VERDICT: PASS" in role.instructions and "VERDICT: CHANGES" in role.instructions
        assert "- [blocker|major|minor] path:line" in role.instructions
    elif role.contract == "status":
        assert "STATUS: READY" in role.instructions and "STATUS: BLOCKED" in role.instructions
    elif role.contract == "spec":
        assert "# <short title>" in role.instructions


@pytest.mark.parametrize("name", ["approver", "fast-approver"])
def test_approval_preserves_escalation_categories(tmp_path, name):
    role = resolve_role(name, "codex", root=tmp_path)
    for category in ("payments", "auth", "permissions", "identity", "database",
                     "storage security rules", "deploy", "CI", "hosting", "build scripts",
                     "migrate a database schema", "send email", "production data", "dependency",
                     "Assumption", "pricing", "business copy", "free versus paid"):
        assert category in role.instructions
    assert "git merge-tree --write-tree main <branch>" in role.instructions


def test_fast_approver_is_an_independent_review_with_conditional_changes(tmp_path):
    fast = resolve_role("fast-approver", "codex", root=tmp_path)
    assert fast.text != resolve_role("approver", "codex", root=tmp_path).text
    for requirement in ("base_head..HEAD", "complete request/rework", "builder Acceptance",
                        "every criterion", "spot-check", "tests, build, lint and type-check",
                        "Check security", "Check correctness", "CHANGES requires onChanges",
                        "APPROVE, CHANGES or ESCALATE"):
        assert requirement in fast.instructions


@pytest.mark.parametrize("name", ["builder", "reviewer-ui", "reviewer-ux"])
def test_browser_roles_use_attempt_session_and_clean_up(tmp_path, name):
    role = resolve_role(name, "codex", root=tmp_path)
    for requirement in ("browser_session", "including close", "assigned ports", "throwaway data",
                        "never kill by process name", "artifacts_dir"):
        assert requirement in role.instructions
