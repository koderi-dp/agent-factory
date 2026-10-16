"""v1 migration, legacy isolation and upgrade recovery (plan §15.6, §16.3)."""

import json
from pathlib import Path
import shutil

import pytest

from agent_factory import __version__, cli, service
from agent_factory.engine import gitops, legacy, scaffold, versioning
from test_engine_api import api, headers, initialise, repo

FIXTURE = Path(__file__).parent / "fixtures/v1"


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def v1(root):
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    (root / "factory/job.py").write_text("# obsolete runtime\n")
    (root / "factory/server.py").write_text("# obsolete runtime\n")
    (root / "factory/dashboard").mkdir()
    (root / "factory/dashboard/index.html").write_text("old dashboard")
    for name in ("factory", "factory-watch", "factory-next", "factory-dashboard"):
        path = root / ".claude/skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("old skill")
    # Unrelated client customisations must survive migration.
    path = root / ".claude/agents/team-agent.md"
    path.write_text("unrelated agent")
    return root


def test_v1_upgrade_dry_run_and_migration(repo, capsys):
    v1(repo)
    before = snapshot(repo)
    assert cli.main(["--repo", str(repo), "upgrade", "--dry-run"]) == 0
    diff = capsys.readouterr().out
    assert "factory/roles/approver.claude.md" in diff
    assert "a/.codex/agents/builder.md" in diff
    assert snapshot(repo) == before
    assert not (repo.parent / "machine/machine.db").exists()
    assert cli.main(["--repo", str(repo), "upgrade"]) == 0
    after = snapshot(repo)
    for name, data in before.items():
        if name.startswith("factory/jobs/") or name == "factory/backlog.md":
            assert after[name] == data
    assert after["factory/roles/approver.claude.md"] == before[".claude/agents/approver.md"]
    assert not (repo / "factory/roles/builder.codex.md").exists()
    assert not (repo / ".claude/agents/approver.md").exists()
    assert not (repo / ".codex/agents/builder.md").exists()
    assert not (repo / "factory/job.py").exists()
    assert not (repo / "factory/dashboard").exists()
    assert not (repo / ".claude/skills/factory-watch").exists()
    assert (repo /
            ".claude/skills/factory/SKILL.md").read_bytes() == (scaffold.TEMPLATES /
                                                                "SKILL.claude.md").read_bytes()
    assert (repo / ".agents/skills/factory/SKILL.md").is_file()
    assert (repo / ".claude/agents/team-agent.md").read_text() == "unrelated agent"
    assert json.loads(after["factory/config.json"])["version"] == 2
    assert scaffold.upgrade(repo) == "Already up to date."
    assert snapshot(repo) == after
    assert scaffold.check_init(repo) == repo.resolve()


def test_upgrade_preserves_overrides_and_refuses_conflicts(repo):
    v1(repo)
    target = repo / "factory/roles/approver.claude.md"
    target.parent.mkdir()
    target.write_text("---\ntype: approval\n---\nDifferent existing override.\n")
    before = snapshot(repo)
    with pytest.raises(ValueError, match="Custom role conflict"):
        scaffold.upgrade(repo)
    assert snapshot(repo) == before


def test_upgrade_v2_updates_gate_and_wrappers_only(repo):
    initialise(repo)
    config = repo / "factory/config.json"
    raw = json.loads(config.read_text())
    raw["engine"] = ">=999,<1000"
    config.write_text(json.dumps(raw))
    override = repo / "factory/roles/builder.codex.md"
    override.parent.mkdir()
    override.write_text("---\ntype: task\n---\nKeep this text.\n")
    before = override.read_bytes()
    helper = repo / "factory/custom-helper.py"
    helper.write_text("# repository-owned helper\n")
    assert cli.main(["--repo", str(repo), "upgrade"]) == 0
    versioning.check(repo)
    assert override.read_bytes() == before
    assert helper.exists()


def test_upgrade_restarts_an_older_service_but_dry_run_does_not(repo, monkeypatch):
    initialise(repo)
    old = {"version": __version__ + ".old"}
    restarted = []
    monkeypatch.setattr(service, "info", lambda: old)
    monkeypatch.setattr(service, "healthy", lambda data: True)
    monkeypatch.setattr(service, "ensure", lambda: restarted.append(True))
    assert cli.main(["--repo", str(repo), "upgrade", "--dry-run"]) == 0
    assert restarted == []
    assert cli.main(["--repo", str(repo), "upgrade"]) == 0
    assert restarted == [True]


def test_upgrade_preserves_configured_custom_role_and_both_provider_variants(repo):
    v1(repo)
    source = repo / ".codex/agents/approver.md"
    source.write_text("---\ntype: approval\n---\nCodex custom approval.\n")
    path = repo / "factory/config.json"
    config = json.loads(path.read_text())
    step = next(step for step in config["pipeline"] if step.get("uses") == "builder")
    step["uses"] = "custom-builder"
    path.write_text(json.dumps(config))
    custom = repo / ".claude/agents/custom-builder.md"
    custom.write_text("---\ntype: task\n---\nCustom builder instructions.\n")
    data = custom.read_bytes()
    # CRLF checkouts must still recognise unmodified original roles.
    original = repo / ".codex/agents/builder.md"
    original.write_bytes(original.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    scaffold.upgrade(repo)
    assert (repo / "factory/roles/custom-builder.claude.md").read_bytes() == data
    assert "Codex custom approval" in (repo / "factory/roles/approver.codex.md").read_text()
    assert "Keep our custom" in (repo / "factory/roles/approver.claude.md").read_text()
    assert not (repo / "factory/roles/builder.codex.md").exists()


def test_legacy_jobs_read_only_reports_and_no_scheduling(api):
    client, runtime, repo_id, root = api
    shutil.copytree(FIXTURE / "factory/jobs", root / "factory/jobs", dirs_exist_ok=True)
    before = snapshot(root / "factory/jobs")
    base = f"/api/repos/{repo_id}"
    state = client.get(base + "/state").json()
    assert state["jobs"] == []
    assert state["legacy_jobs"] == [{
        "id": "007-legacy",
        "title": "Old job",
        "stage": "build",
        "read_only": True,
        "reports": ["build.md"]
    }]
    assert client.get(base + "/jobs/007-legacy").json()["legacy"]["read_only"]
    assert "Legacy evidence" in client.get(base + "/jobs/007-legacy/files/build.md").text
    for action in ("resume", "stop", "rework"):
        response = client.post(base + "/jobs/007-legacy/" + action, json={}, headers=headers())
        assert response.status_code == 400 and "read-only" in response.text
    for _ in range(2):
        runtime.tick()
    assert snapshot(root / "factory/jobs") == before
    assert runtime.scheduler.claim_next() is None
    assert client.post(base + "/jobs/007-legacy/approve", json={},
                       headers=headers()).status_code == 400
    assert client.post(base + "/jobs/007-legacy/delete", json={},
                       headers=headers()).status_code == 400
    assert client.post(base + "/jobs/007-legacy/delete", json={
        "force": True
    }, headers=headers()).status_code == 200
    after = client.get(base + "/state").json()
    assert after["legacy_jobs"] == []
    assert after["last_event_id"] > state["last_event_id"]


def test_new_job_sequence_skips_legacy_jobs(api):
    client, _, repo_id, root = api
    shutil.copytree(FIXTURE / "factory/jobs", root / "factory/jobs", dirs_exist_ok=True)
    result = client.post(f"/api/repos/{repo_id}/jobs", json={"title": "Legacy"}, headers=headers())
    assert result.status_code == 201
    assert result.json()["id"] == "008-legacy"
    assert legacy.load(root, "007-legacy")["stage"] == "build"


def test_legacy_merge_uses_ported_git_checks(api):
    client, _, repo_id, root = api
    job_id = "007-legacy"
    path, base = gitops.create_worktree(root, job_id)
    (path / "legacy.txt").write_text("Completed before migration\n")
    gitops.checked("add", ".", cwd=path)
    gitops.checked("commit", "-qm", "Legacy change", cwd=path)
    approved = gitops.head(path)
    folder = root / "factory/jobs" / job_id
    folder.mkdir(parents=True)
    value = {
        "id": job_id,
        "feature": "Old feature",
        "worktree": str(path),
        "branch": f"factory/{job_id}",
        "stage": "needs-human",
        "pipeline": [],
        "pipelineComplete": True,
        "approval": {
            "head": base
        }
    }
    (folder / "job.json").write_text(json.dumps(value))
    route = f"/api/repos/{repo_id}/jobs/{job_id}/approve"
    response = client.post(route, json={}, headers=headers())
    assert response.status_code == 400 and "branch changed" in response.text
    value["approval"]["head"] = approved
    (folder / "job.json").write_text(json.dumps(value))
    assert client.post(route, json={}, headers=headers()).status_code == 200
    assert (root / "legacy.txt").read_text().startswith("Completed")
    assert legacy.load(root, job_id)["stage"] == "merged"
    assert not path.exists()


def test_v1_new_work_requires_upgrade(repo):
    v1(repo)
    with pytest.raises(versioning.VersionMismatch, match="factory upgrade"):
        versioning.check(repo, new_jobs=True)
