"""Full v2 sandbox acceptance with real git/SQLite/processes and the fake Codex CLI."""

import json
import os

import pytest

from tools import sandbox
from agent_factory.engine import pipeline, ports
from test_engine_gitops import repo, add_job, context_for


def test_full_six_jobs_across_two_repos(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp("sb")
    original_home = str(tmp_path / "unrelated-machine")
    monkeypatch.setenv("FACTORY_HOME", original_home)
    suite = tmp_path / "sandbox č with spaces"
    sandbox.new(suite)
    result = sandbox.run(suite,
                         timeout=240,
                         port_range="32000-32899" if os.name == "nt" else "33000-33899")
    assert os.environ["FACTORY_HOME"] == original_home
    assert result["jobs"] == 6 and result["attempts"] == 42
    assert result["peak_live"] == 6 and result["peak_repos"] == 2
    assert result["all_merged"]
    assert result["claude_parity"] == "pending session 1.11"
    assert json.loads((suite / "result.json").read_text(encoding="utf-8")) == result
    browser_sessions = set()
    for root in sandbox.repos(suite):
        connection = pipeline.open_repo(root)
        try:
            jobs = [dict(row) for row in connection.execute("SELECT * FROM jobs")]
            assert len(jobs) == 3 and {job["state"] for job in jobs} == {"merged"}
            executions = [dict(row) for row in connection.execute("SELECT * FROM executions")]
            attempts = [dict(row) for row in connection.execute("SELECT * FROM attempts")]
            assert len(executions) == len(attempts) == 21
            assert {row["state"] for row in attempts} == {"valid"}
            assert {row["n"] for row in attempts} == {1}
            assert {row["kind"] for row in attempts} == {"fresh"}
            assert {json.loads(row["usage"])["input"] for row in attempts} == {10}
            for execution in executions:
                attempt = next(row for row in attempts if row["execution_id"] == execution["id"])
                directory = root / "factory/jobs" / execution["job_id"] / attempt["dir"]
                assert len(list((directory / "invocations").glob("*.json"))) == 1
                assigned = json.loads((directory / "bound-ports.json").read_text(encoding="utf-8"))
                packet = json.loads((directory / "packet.json").read_text(encoding="utf-8"))
                session = packet["paths"]["browser_session"]
                assert session not in browser_sessions
                browser_sessions.add(session)
                assert packet["paths"]["artifacts_dir"] == str(directory.resolve())
                assert packet["paths"]["ports"] == (", ".join(map(str, assigned)) or "none")
                if execution["uses"] == "builder":
                    assert "The requested feature works" in packet["acceptance_criteria"]
                assert (directory / "report.md").exists()
        finally:
            connection.close()
        assert not sandbox.gitops.checked("worktree", "list", "--porcelain",
                                          cwd=root).count("factory/")
        # Backlog checkboxes are expected user-visible edits on main.
        assert "- [ ]" not in (root / "factory/backlog.md").read_text(encoding="utf-8")
        sandbox.verify_main(root)
    bases = [row[2] for row in result["leases"]]
    assert len(bases) == 6 and len(set(bases)) == 6
    assert not ports.listeners(range(32000, 32060) if os.name == "nt" else range(33000, 33060))
    with pytest.raises(ValueError, match="already has jobs"):
        sandbox.run(suite)
    assert os.environ["FACTORY_HOME"] == original_home
    continued = sandbox.run(suite, resume=True)
    assert continued["attempts"] == 42 and continued["resumed"]
    assert continued["peak_repos"] == 2
    root = sandbox.repos(suite)[0]
    with (root / "src/app.js").open("a") as stream:
        stream.write("\n// unintended main edit\n")
    with pytest.raises(AssertionError, match="main is dirty"):
        sandbox.verify_main(root)


def test_controlled_real_command_keeps_production_flags_and_disables_user_config(repo):
    context = context_for(repo, add_job(repo))
    adapter = sandbox.SandboxCodexAdapter()
    command = adapter.command(context)
    assert command["argv"][1:3] == ["exec", "--ignore-user-config"]
    assert command["argv"].count("--ignore-user-config") == 1
    assert "--json" in command["argv"]
    assert "--ignore-rules" not in command["argv"]
    assert command["argv"][-1] == "-"
    assert command["cwd"] == context.job["worktree"]


def test_resume_requires_a_six_job_suite(tmp_path):
    suite = tmp_path / "empty-suite"
    sandbox.new(suite)
    with pytest.raises(ValueError, match="existing six-job suite"):
        sandbox.run(suite, resume=True)
