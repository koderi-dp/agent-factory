"""Session 1.9: real SQLite/git with an isolated machine and local HTTP API."""

import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from agent_factory import __version__, cli, service
from agent_factory.api.app import BODY_LIMIT, create_app
from agent_factory.engine import db, gitops, pipeline, registry, scaffold, versioning
from agent_factory.engine.locking import FileLock
from agent_factory.engine.runtime import Runtime
from agent_factory.paths import machine_dir


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "project č"
    root.mkdir()
    monkeypatch.chdir(root)
    for args in [("init", "-qb", "main"), ("config", "user.name", "test"),
                 ("config", "user.email", "test@localhost"), ("config", "commit.gpgsign", "false"),
                 ("config", "core.autocrlf", "false")]:
        gitops.checked(*args, cwd=root)
    (root / "AGENTS.md").write_text("Run checks. Use the assigned PORT.\n")
    (root / "app.txt").write_text("base\n")
    gitops.checked("add", ".", cwd=root)
    gitops.checked("commit", "-qm", "initial", cwd=root)
    yield root
    # The temp root includes all worktrees; release Git handles on Windows too.
    for line in gitops.checked("worktree", "list", "--porcelain", cwd=root).splitlines():
        if line.startswith("worktree ") and Path(line[9:]).resolve() != root.resolve():
            gitops.git("worktree", "remove", "--force", line[9:], cwd=root)


def initialise(root):
    scaffold.init(root, "codex")
    gitops.checked("add", ".", cwd=root)
    gitops.checked("commit", "-qm", "factory setup", cwd=root)
    connection = registry.open_registry()
    try:
        return registry.register(connection, root).id
    finally:
        connection.close()


@pytest.fixture
def api(repo):
    repo_id = initialise(repo)
    runtime = Runtime()
    app = create_app(port=5099, token="test-token", runtime=runtime, run_engine=False)
    try:
        with TestClient(app, base_url="http://127.0.0.1:5099") as client:
            yield client, runtime, repo_id, repo
    finally:
        runtime.close()


def headers(**kwargs):
    return {"X-Factory-Token": "test-token", **kwargs}


def test_security_host_origin_and_token(api):
    client, _, repo_id, _ = api
    for host in ["evil.test:5099", "127.0.0.1:80", "localhost", "127.0.0.1:5099.evil.test"]:
        assert client.get("/api/health", headers={"Host": host}).status_code == 403
        assert client.get("/", headers={"Host": host}).status_code == 403
    assert client.get("/api/health", headers={"Host": "localhost:5099"}).status_code == 200
    path = f"/api/repos/{repo_id}/jobs"
    for origin in ["http://evil.test", "null", "http://localhost:5099", "https://127.0.0.1:5099"]:
        response = client.post(path, json={"title": "Bad origin"}, headers=headers(Origin=origin))
        assert response.status_code == 403
    for token in [None, "wrong"]:
        assert client.post(path,
                           json={
                               "title": "missing"
                           },
                           headers={} if token is None else {
                               "X-Factory-Token": token
                           }).status_code == 403
    for origin in [None, "http://127.0.0.1:5099"]:
        response = client.post(path,
                               json={"title": "Valid"},
                               headers=headers(**({
                                   "Origin": origin
                               } if origin else {})))
        assert response.status_code == 201
    response = client.options(path,
                              headers={
                                  "Origin": "http://evil.test",
                                  "Access-Control-Request-Method": "POST",
                                  "Access-Control-Request-Headers": "X-Factory-Token"
                              })
    assert not any(key.lower().startswith("access-control-") for key in response.headers)


def test_body_limit_including_chunked_and_duplicate_headers(api):
    client, _, repo_id, _ = api
    path = f"/api/repos/{repo_id}/jobs"
    assert BODY_LIMIT == 100_000  # Preserve the v1 limit, in bytes.
    assert client.post(path, content=b"x" * (BODY_LIMIT + 1), headers=headers()).status_code == 413

    def chunks():
        yield b"x" * BODY_LIMIT
        yield b"x"

    assert client.post(path, content=chunks(), headers=headers()).status_code == 413
    assert client.get("/api/health", headers=[("host", "127.0.0.1:5099"),
                                              ("host", "evil")]).status_code == 403
    assert client.post(path,
                       json={
                           "title": "dup"
                       },
                       headers=[("X-Factory-Token", "test-token"),
                                ("X-Factory-Token", "test-token")]).status_code == 403


def test_unknown_repo_and_confined_files(api, tmp_path):
    client, _, repo_id, root = api
    assert client.get("/api/repos/unknown/state").status_code == 404
    assert client.post("/api/repos/unknown/jobs", json={
        "title": "x"
    }, headers=headers()).status_code == 404
    job_id = client.post(f"/api/repos/{repo_id}/jobs", json={
        "title": "Files"
    }, headers=headers()).json()["id"]
    prefix = f"/api/repos/{repo_id}/jobs/{job_id}/files/"
    assert "Files" in client.get(prefix + "request.md").text
    for path in ["%2e%2e%2f%2e%2e%2fconfig.json", "%2Fetc%2Fpasswd", "..%5c..%5cconfig.json"]:
        assert client.get(prefix + path).status_code in {403, 404}
    secret = root / "factory/jobs" / job_id / "command.json"
    secret.write_text('{"token":"private"}')
    assert client.get(prefix + "command.json").status_code == 403
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret")
    link = root / "factory/jobs" / job_id / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pass  # Symlink privilege is not always enabled on Windows.
    else:
        assert client.get(prefix + "link.md").status_code == 403


def test_snapshots_config_roles_logs_and_safe_html(api):
    client, runtime, repo_id, root = api
    base = f"/api/repos/{repo_id}"
    job_id = client.post(base + "/jobs",
                         json={
                             "title": "<script>alert(1)</script>"
                         },
                         headers=headers()).json()["id"]
    page = client.get("/")
    assert "&lt;script&gt;" in page.text and "<script>" not in page.text
    assert "test-token" in page.text
    snapshot = client.get(base + "/state").json()
    assert snapshot["jobs"][0]["id"] == job_id
    assert snapshot["last_event_id"] > 0
    assert client.get("/api/overview").json()["repos"][0]["counts"]["queued"] == 1
    assert client.get(base + f"/jobs/{job_id}").json()["attempts"] == []
    assert client.get(base + "/roles").json()["roles"]
    old = client.get(base + "/config").json()
    invalid = {**old, "maxRounds": 100}
    assert client.put(base + "/config", json=invalid, headers=headers()).status_code == 422
    changed = {**old, "maxRounds": 2}
    assert client.put(base + "/config", json=changed, headers=headers()).status_code == 200
    assert client.get(base +
                      f"/jobs/{job_id}").json()["job"]["frozen"]["maxRounds"] == old["maxRounds"]
    assert client.get(base + "/attempts/999/log").status_code == 404
    assert client.get(base + "/attempts/999/log?offset=-1").status_code == 422
    writer = runtime.repo(repo_id)[1]
    writer.call(
        lambda connection: pipeline.advance_job(connection, job_id, current_head=gitops.head(root)))
    claim = runtime.scheduler.claim_next()
    claim.directory.mkdir(parents=True)
    (claim.directory / "events.jsonl").write_bytes(b"a" * (300 * 1024))
    response = client.get(base + f"/attempts/{claim.attempt['id']}/log?offset=5").json()
    assert len(response["text"]) == 256 * 1024
    assert response["offset"] == 5 + 256 * 1024
    attempts = client.get(base + f"/jobs/{job_id}").json()["attempts"]
    assert "owner_token" not in attempts[0]


@pytest.mark.parametrize("provider,wrappers", [
    ("codex", [".agents/skills/factory/SKILL.md"]),
    ("claude", [".claude/skills/factory/SKILL.md"]),
    ("both", [".agents/skills/factory/SKILL.md", ".claude/skills/factory/SKILL.md"]),
])
def test_init_exact_files_idempotent_and_dry_run(repo, provider, wrappers):
    before = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
    }
    scaffold.init(repo, provider, dry_run=True)
    assert {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
    } == before
    assert not machine_dir().exists()
    scaffold.init(repo, provider)
    after = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
    }
    assert set(after) - set(before) == {
        "factory/config.json", "factory/backlog.md", ".gitignore", *wrappers
    }
    assert after[".gitignore"] == b"/factory/state/\n/factory/jobs/\n"
    for path in wrappers:
        assert b"uv tool install agent-factory" in after[path]
        assert b"keeps running" in after[path]
    messages = scaffold.init(repo, provider)
    assert all("unchanged" in message for message in messages)
    assert {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
    } == after
    connection = registry.open_registry()
    assert len(registry.list_repos(connection)) == 1
    connection.close()


def test_init_preconditions_and_v1_do_not_write(repo):
    (repo / "AGENTS.md").unlink()
    with pytest.raises(ValueError, match="AGENTS.md"):
        scaffold.init(repo, "codex")
    assert not (repo / "factory").exists()
    (repo / "CLAUDE.md").write_text("checks")
    gitops.checked("branch", "-m", "other", cwd=repo)
    with pytest.raises(ValueError, match="main branch"):
        scaffold.init(repo, "codex")
    assert not (repo / "factory").exists()
    gitops.checked("branch", "-m", "main", cwd=repo)
    (repo / "factory").mkdir()
    (repo / "factory/job.py").write_text("# legacy evidence")
    with pytest.raises(ValueError, match="factory upgrade"):
        scaffold.init(repo, "codex")
    assert not (repo / "factory/config.json").exists()


def test_backlog_details_duplicate_start_and_queue_replay(api):
    client, runtime, repo_id, root = api
    base = f"/api/repos/{repo_id}"
    # Replace the template example with deliberate test items.
    path = root / "factory/backlog.md"
    path.write_text("# Backlog\n\n- [ ] One\n  detailed **request**\n- [ ] Two\n")
    response = client.post(base + "/backlog",
                           json={
                               "action": "start",
                               "index": 0
                           },
                           headers=headers())
    assert response.status_code == 200
    job_id = response.json()["id"]
    assert f"- [x] One ({job_id})" in path.read_text()
    assert "detailed **request**" in (root / "factory/jobs" / job_id / "request.md").read_text()
    # A crash after DB commit but before checking off the file is repaired.
    path.write_text("# Backlog\n\n- [ ] One\n  detailed **request**\n- [ ] Two\n")
    response = client.post(base + "/backlog",
                           json={
                               "action": "start",
                               "index": 0
                           },
                           headers=headers())
    assert response.status_code == 400 and "already" in response.json()["detail"]
    runtime._drain(repo_id, root, runtime.repo(repo_id)[1])
    assert f"- [x] One ({job_id})" in path.read_text()
    assert len(client.get(base + "/state").json()["jobs"]) == 1
    for body in [{
            "action": "add",
            "items": [{
                "text": "Three",
                "details": "details"
            }]
    }, {
            "action": "move",
            "index": 1,
            "to": 0
    }, {
            "action": "remove",
            "index": 1
    }]:
        assert client.post(base + "/backlog", json=body, headers=headers()).status_code == 200
    assert client.get(base + "/state").json()["backlog"][0]["title"] == "Three"


def test_job_stop_resume_delete_and_unsupported_provider(api):
    client, runtime, repo_id, root = api
    base = f"/api/repos/{repo_id}"
    job_id = client.post(base + "/jobs", json={"title": "Stop"}, headers=headers()).json()["id"]
    writer = runtime.repo(repo_id)[1]
    writer.call(
        lambda connection: pipeline.advance_job(connection, job_id, current_head=gitops.head(root)))
    assert client.post(base + f"/jobs/{job_id}/stop",
                       headers=headers()).json()["job"]["state"] == "paused"
    assert client.post(base + f"/jobs/{job_id}/resume",
                       headers=headers()).json()["job"]["state"] == "running"
    assert client.post(base + f"/jobs/{job_id}/delete", headers=headers()).status_code == 400
    assert client.post(base + f"/jobs/{job_id}/delete", json={
        "force": True
    }, headers=headers()).status_code == 200
    assert not (root / "factory/jobs" / job_id).exists()
    config = json.loads((root / "factory/config.json").read_text())
    config["pipelines"]["fast"][0]["provider"] = "claude"
    (root / "factory/config.json").write_text(json.dumps(config))
    response = client.post(base + "/jobs",
                           json={
                               "title": "No Claude",
                               "pipeline": "fast"
                           },
                           headers=headers())
    assert response.status_code == 400 and "Unsupported provider: claude" in response.json(
    )["detail"]


@pytest.mark.parametrize("version,spec,expected", [
    ("1.4.2", ">=1.0,<2", True),
    ("2.1.0", ">=1.0,<2", False),
    ("1.4.2", "~=1.4", True),
    ("1.5.0", "~=1.4.2", False),
    ("1.4.2", "==1.4.*", True),
    ("1.4.2", "!=1.4.*", False),
    ("1.0", "==1.0.0", True),
    ("1.0+build.2", "==1.0", True),
    ("1.0rc1", ">=1.0rc1,<2", True),
    ("1.0rc1", ">=1.0,<2", False),
    ("1.0.post1", ">1.0", False),
    ("1.0.dev1", "<1.0", False),
    ("2!1.0", ">=1!2", True),
    ("1.0", "===1.0", True),
    ("1.0.post2", ">1.0.post1", True),
    ("1.0.0.0.0.0.0.0.0", "==1.0", True),
    ("1.0.2", "==1.0.*", True),
    ("1.0.2", "~=1.0.0", True),
])
def test_engine_specifiers(version, spec, expected):
    assert versioning.matches(version, spec) is expected


@pytest.mark.parametrize("specifier", ["1.0", ">=1+local", "==1rc1.*", "~=1"])
def test_invalid_engine_specifiers(specifier):
    with pytest.raises(ValueError):
        versioning.matches("1.1", specifier)


def test_cli_version_gate_exit_codes_registration_and_repo_id(repo, monkeypatch, capsys):
    repo_id = initialise(repo)
    raw = json.loads((repo / "factory/config.json").read_text())
    raw["engine"] = ">=1.0,<2"
    (repo / "factory/config.json").write_text(json.dumps(raw))
    monkeypatch.setattr(versioning, "__version__", "2.1.0")
    with pytest.raises(versioning.VersionMismatch) as error:
        versioning.check(repo, version="2.1.0")
    assert str(error.value) == (f"{repo.name} needs agent-factory >=1.0,<2; installed 2.1.0. "
                                "Install a matching version: `uv tool install agent-factory==1.*`")
    assert cli.main(["status"]) == 4
    assert "Install a matching version" in capsys.readouterr().err
    raw["engine"] = ">=0"
    (repo / "factory/config.json").write_text(json.dumps(raw))
    connection = registry.open_registry()
    registry.remove(connection, repo_id)
    assert cli.main(["version"]) == 0
    assert registry.list_repos(connection)[0].id == repo_id
    assert cli.main(["--repo", repo_id, "repos", "rename", repo_id, "Friendly"]) == 0
    assert registry.list_repos(connection)[0].name == "Friendly"
    connection.close()
    monkeypatch.chdir(repo.parent)
    assert cli.main(["status"]) == 5


def test_file_lock_single_instance(tmp_path):
    first, second = FileLock(tmp_path / "lock"), FileLock(tmp_path / "lock")
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        second.close()


def test_real_service_auto_start_second_serve_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    monkeypatch.setenv("FACTORY_PORT", "0")
    monkeypatch.chdir(tmp_path)
    data = None
    try:
        assert cli.main(["service", "status"]) == 0
        data = service.ensure()
        assert service.healthy(data)
        assert service.request(data, "/api/health")["version"] == __version__
        assert set(json.loads(
            (machine_dir() /
             "service.json").read_text())) == {"pid", "create_time", "port", "token", "version"}
        result = subprocess.run([sys.executable, "-m", "agent_factory.cli", "serve"],
                                capture_output=True,
                                text=True,
                                timeout=15)
        assert result.returncode == 0 and service.url(data) in result.stdout
        assert service.info()["pid"] == data["pid"]
        assert cli.main(["config", "--machine", "concurrency.global=3"]) == 0
        assert service.request(data, "/api/machine/settings")["concurrency"]["global"] == 3
        assert cli.main(["service", "restart"]) == 0
        restarted = service.info()
        assert restarted["pid"] != data["pid"]
        assert restarted["port"] == data["port"]
        data = restarted
    finally:
        current = service.info()
        if service.healthy(current):
            service.stop(current)
    assert not (machine_dir() / "service.json").exists()


def test_service_version_mismatch_restarts_real_process(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    monkeypatch.chdir(tmp_path)
    old_version = "0.0.0" if __version__ != "0.0.0" else "0.0.1"
    code = (f"import agent_factory; agent_factory.__version__={old_version!r}; "
            "from agent_factory.service import serve; raise SystemExit(serve(0))")
    child = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not service.healthy(service.info()):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        old = service.info()
        assert old["version"] == old_version
        current = service.ensure()
        assert current["version"] == __version__
        assert current["pid"] != old["pid"]
        assert current["port"] == old["port"]
        child.wait(timeout=10)
        assert child.returncode == 0
    finally:
        current = service.info()
        if service.healthy(current):
            service.stop(current)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def test_cli_dispatches_via_api_and_waits(api, monkeypatch, capsys, tmp_path):
    client, _, repo_id, root = api
    data = {"port": 5099, "token": "test-token"}
    ensured = []
    monkeypatch.setattr(service, "ensure", lambda: ensured.append(True) or data)

    def http(data, path, body=None, *, method=None):
        response = client.request(method or ("POST" if body is not None else "GET"),
                                  path,
                                  json=body,
                                  headers=headers())
        if response.status_code >= 400:
            raise service.ServiceError(str(response.json()["detail"]),
                                       response.json().get("exit_code", 1))
        return response.json()

    monkeypatch.setattr(service, "request", http)
    details = tmp_path / "details.txt"
    details.write_text("Full request č", encoding="utf-8")
    assert cli.main([
        "--repo", repo_id, "start", "Via CLI", "--details-file",
        str(details), "--pipeline", "fast"
    ]) == 0
    output = capsys.readouterr().out
    assert "001-via-cli" in output and "/#/repo/" in output
    assert "Full request č" in (root /
                                "factory/jobs/001-via-cli/request.md").read_text(encoding="utf-8")
    assert cli.main(["status", "001-via-cli", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["job"]["pipeline_name"] == "fast"
    assert cli.main(["next"]) == 0
    assert "002-describe-your-next-feature" in capsys.readouterr().out
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.main(["dashboard"]) == 0
    assert repo_id in opened[0]
    assert ensured

    states = iter([("queued", None), ("running", "build"), ("paused", "build")])

    def response(*args):
        state, step = next(states)
        return {
            "job": {
                "state": state,
                "current_step": step,
                "round": 1,
                "pause_reason": "auth" if state == "paused" else None
            }
        }

    monkeypatch.setattr(service, "request", response)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    assert cli._wait(data, f"/api/repos/{repo_id}", "001-via-cli") == 0
    assert "(auth)" in capsys.readouterr().out


def complete_fast(runtime, repo_id, job_id):
    root, writer = runtime.repo(repo_id)
    job = writer.call(lambda connection: pipeline.load(connection, job_id)[0])
    tree = Path(job["worktree"])
    (tree / "app.txt").write_text(f"job change {job_id}\n")
    gitops.checked("commit", "-qam", "build", cwd=tree)
    head = gitops.head(tree)
    writer.call(
        lambda connection: pipeline.advance_job(connection, job_id, current_head=job["base_head"]))
    for result in ["READY", "APPROVE"]:

        def finish(connection):
            job, executions = pipeline.load(connection, job_id)
            execution = next(e for e in executions if e["step_id"] == job["current_step"])
            with db.tx(connection):
                connection.execute("UPDATE executions SET state='running' WHERE id=?",
                                   (execution["id"], ))
            pipeline.advance_job(
                connection,
                job_id,
                current_head=head,
                results={execution["id"]: pipeline.Completion(result, head, "report.md")})

        writer.call(finish)
    return writer.call(lambda connection: pipeline.load(connection, job_id)[0])


def test_approve_busy_exit_code_and_rework(api, monkeypatch, capsys):
    client, runtime, repo_id, root = api
    base = f"/api/repos/{repo_id}/jobs"
    first = client.post(base, json={
        "title": "Merge",
        "pipeline": "fast"
    }, headers=headers()).json()["id"]
    assert complete_fast(runtime, repo_id, first)["state"] == "needs_human"
    gitops.checked("checkout", "-qb", "busy", cwd=root)
    response = client.post(base + f"/{first}/approve", headers=headers())
    assert response.status_code == 409 and response.json()["exit_code"] == 3
    gitops.checked("checkout", "-q", "main", cwd=root)
    assert client.post(base + f"/{first}/approve", headers=headers()).status_code == 200
    assert client.get(base + f"/{first}").json()["job"]["state"] == "merged"
    assert not gitops.worktree_path(root, first).exists()
    second = client.post(base, json={
        "title": "Rework",
        "pipeline": "fast"
    }, headers=headers()).json()["id"]
    complete_fast(runtime, repo_id, second)
    response = client.post(base + f"/{second}/rework",
                           json={"note": "Change the approach"},
                           headers=headers())
    assert response.status_code == 200
    assert response.json()["job"]["round"] == 2
    assert (root / "factory/jobs" / second /
            "rework-1.md").read_text().strip() == "Change the approach"


def test_queue_relinks_existing_job_without_recreating_worktree(api):
    client, runtime, repo_id, root = api
    job_id = client.post(f"/api/repos/{repo_id}/jobs", json={
        "title": "Recover"
    }, headers=headers()).json()["id"]
    writer = runtime.repo(repo_id)[1]
    request = root / "factory/jobs" / job_id / "request.md"
    request.write_text("Retain this evidence")

    def interrupt(connection):
        with db.tx(connection):
            connection.execute("UPDATE start_queue SET job_id=NULL WHERE job_id=?", (job_id, ))

    writer.call(interrupt)
    gitops.git("worktree", "remove", "--force", str(gitops.worktree_path(root, job_id)), cwd=root)
    raw = json.loads((root / "factory/config.json").read_text())
    raw["engine"] = ">=999"
    (root / "factory/config.json").write_text(json.dumps(raw))
    runtime._drain(repo_id, root, writer)
    assert not gitops.worktree_path(root, job_id).exists()
    assert request.read_text() == "Retain this evidence"
    assert writer.call(lambda connection: connection.execute("SELECT job_id FROM start_queue").
                       fetchone()[0]) == job_id


@pytest.mark.parametrize("merge", ["auto", "verify"])
def test_sensitive_fast_merge_through_api(api, merge):
    client, runtime, repo_id, root = api
    config_path = root / "factory/config.json"
    config = json.loads(config_path.read_text())
    config.update(merge=merge, sensitivePaths=["app.txt"])
    config_path.write_text(json.dumps(config), encoding="utf-8")
    base = f"/api/repos/{repo_id}/jobs"
    job_id = client.post(base, json={
        "title": "Sensitive",
        "pipeline": "fast"
    }, headers=headers()).json()["id"]
    complete_fast(runtime, repo_id, job_id)
    config["sensitivePaths"] = []
    config_path.write_text(json.dumps(config), encoding="utf-8")
    if merge == "auto":
        runtime.tick()
        job = client.get(base + f"/{job_id}").json()["job"]
        assert (job["state"], job["pause_reason"]) == ("needs_human", "sensitive")
        assert "app.txt" in job["note"]
    response = client.post(base + f"/{job_id}/approve", headers=headers())
    assert response.status_code == 400
    assert "app.txt" in response.json()["detail"]
    response = client.post(base + f"/{job_id}/approve",
                           json={"confirm_sensitive": True},
                           headers=headers())
    assert response.status_code == 200
    assert client.get(base + f"/{job_id}").json()["job"]["state"] == "merged"


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_status_with_invalid_current_roles(api, monkeypatch, capsys, json_output):
    client, _, repo_id, root = api
    base = f"/api/repos/{repo_id}"
    job_id = client.post(base + "/jobs",
                         json={
                             "title": "Frozen job",
                             "pipeline": "fast"
                         },
                         headers=headers()).json()["id"]
    config_path = root / "factory/config.json"
    config = json.loads(config_path.read_text())
    config["pipelines"]["fast"][0]["uses"] = "missing-role"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    assert client.get(base + f"/jobs/{job_id}").status_code == 200
    assert client.get(base + "/roles").status_code == 422
    monkeypatch.setattr(service, "ensure", lambda: {"port": 5099, "token": "test-token"})

    def http(data, path):
        response = client.get(path)
        if response.status_code >= 400:
            raise service.ServiceError(str(response.json()["detail"]))
        return response.json()

    monkeypatch.setattr(service, "request", http)
    args = ["status", job_id] + (["--json"] if json_output else [])
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert "role provenance unavailable" in output.err
    assert "missing-role" in output.err
    if json_output:
        result = json.loads(output.out)
        job = result["job"]
        assert job["id"] == job_id
        assert job["title"] == "Frozen job"
        assert "roles" not in result  # Unavailable provenance is not an empty roles list.
    else:
        assert f"{job_id}\tqueued\t\tFrozen job" in output.out


def test_eject_cli_and_outdated_status(api, tmp_path, monkeypatch, capsys):
    from agent_factory.engine import roles
    client, _, repo_id, root = api
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    for source in roles.BUILTIN_DIR.glob("*.md"):
        (builtins / source.name).write_bytes(source.read_bytes())
    monkeypatch.setattr(roles, "BUILTIN_DIR", builtins)
    assert cli.main(["eject", "builder"]) == 0
    capsys.readouterr()
    assert cli.main(["eject", "builder", "--diff"]) == 0
    assert capsys.readouterr().out.strip() == ""
    builtin = builtins / "builder.md"
    builtin.write_bytes(builtin.read_bytes() + b"\nNew check.\n")
    assert cli.main(["eject", "builder", "--diff"]) == 0
    assert "+New check." in capsys.readouterr().out
    data = {"port": 5099, "token": "test-token"}
    monkeypatch.setattr(service, "ensure", lambda: data)
    monkeypatch.setattr(service, "request", lambda data, path: client.get(path).json())
    assert cli.main(["status"]) == 0
    assert "ejected; built-in has changed since" in capsys.readouterr().out
    assert cli.main(["status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(role["name"] == "builder" and role["outdated"] for role in result["roles"])
