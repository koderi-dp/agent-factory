"""Real git repositories: v1 merge/delete cases and plan §7.2 checks."""

from pathlib import Path
import json
import socket
import subprocess
import sys
import time

import pytest

from agent_factory import run_cli
from agent_factory.engine import db, gitops, packet, pipeline, ports, registry, reports, winjob
from agent_factory.engine.config import RepoConfig
from agent_factory.engine.scheduler import Claim


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "app č"
    root.mkdir()
    (root / "factory").mkdir()
    (root / "factory/config.json").write_text("{}", encoding="utf-8")
    (root / "app.txt").write_text("one\n", encoding="utf-8")
    (root / ".gitignore").write_text("/factory/state/\n/factory/jobs/\n", encoding="utf-8")
    (root / "factory/backlog.md").write_text("# Backlog\n", encoding="utf-8")
    for args in [("init", "-q", "-b", "main"), ("config", "user.name", "test"),
                 ("config", "user.email", "test@localhost"), ("config", "core.autocrlf", "false"),
                 ("config", "commit.gpgsign", "false"), ("add", "."), ("commit", "-qm", "base")]:
        gitops.checked(*args, cwd=root)
    connection = pipeline.open_repo(root)
    machine = registry.open_registry()
    repo_id = registry.register(machine, root).id
    yield root, connection, machine, repo_id
    for line in gitops.checked("worktree", "list", "--porcelain", cwd=root).splitlines():
        if line.startswith("worktree ") and Path(line[9:]) != root:
            gitops.git("worktree", "remove", "--force", line[9:], cwd=root)
    machine.close()
    connection.close()


def add_job(repo, job_id="001-test", *, state="approved", change=True):
    root, connection, _, _ = repo
    profile = {"codex": {"model": "fake", "effort": "low"}}
    config = RepoConfig.model_validate(
        {
            "version": 2,
            "engine": ">=0",
            "defaultPipeline": "fast",
            "pipelines": {
                "fast": [
                    {
                        "id": "build",
                        "uses": "builder",
                        "provider": "codex",
                        "profiles": profile
                    },
                    {
                        "id": "approve",
                        "uses": "fast-approver",
                        "provider": "codex",
                        "profiles": profile
                    },
                ]
            }
        },
        context={"root": root})
    worktree, base = gitops.create_worktree(root, job_id)
    pipeline.create_job(connection,
                        job_id,
                        "Do a thing",
                        config=config,
                        worktree=str(worktree),
                        branch="factory/" + job_id,
                        base_head=base,
                        preamble=packet.PREAMBLE,
                        preamble_version=packet.PREAMBLE_VERSION)
    folder = root / "factory/jobs" / job_id
    folder.mkdir(parents=True)
    (folder / "request.md").write_bytes(b"Verbatim request\r\nwith details.\n")
    if change:
        (worktree / "app.txt").write_text("one\nfrom the job\n", encoding="utf-8")
        gitops.checked("commit", "-qam", "job change", cwd=worktree)
    current = gitops.head(worktree)
    pipeline.advance_job(connection, job_id, current_head=current)
    with db.tx(connection):
        connection.execute("UPDATE jobs SET state=?,approved_head=? WHERE id=?",
                           (state, current, job_id))
    return pipeline.load(connection, job_id)[0]


def context_for(repo, job):
    root, connection, _, repo_id = repo
    execution = pipeline.load(connection, job["id"])[1][0]
    return Claim(repo_id, root, job, execution, {
        "id": 1,
        "n": 1,
        "dir": "executions/build.build/r1/a1",
        "kind": "fresh"
    })


def test_merge_cleans_worktree_branch_and_releases_lease(repo):
    root, connection, machine, repo_id = repo
    job = add_job(repo)
    ports.lease(machine, repo_id, job["id"], ())
    message = gitops.merge(root, connection, job["id"], machine=machine, repo_id=repo_id)
    assert "merged factory/001-test" in message
    assert pipeline.load(connection, job["id"])[0]["state"] == "merged"
    assert not Path(job["worktree"]).exists()
    assert not gitops.branch_exists(root, job["branch"])
    assert "from the job" in (root / "app.txt").read_text()
    assert not machine.execute("SELECT * FROM port_leases").fetchall()
    assert len(gitops.checked("rev-list", "--parents", "-1", "HEAD", cwd=root).split()) == 3


def test_unrelated_uncommitted_edits_do_not_block(repo):
    root, connection, _, _ = repo
    job = add_job(repo)
    (root / "factory/backlog.md").write_text("edited\n")
    gitops.merge(root, connection, job["id"])
    assert (root / "factory/backlog.md").read_text() == "edited\n"


@pytest.mark.parametrize("reason", ["overlap", "staged", "branch", "detached"])
def test_busy_merge_cases_preserve_everything(repo, reason):
    root, connection, _, _ = repo
    job = add_job(repo)
    if reason == "overlap":
        (root / "app.txt").write_text("mine\n")
    elif reason == "staged":
        (root / "unrelated.txt").write_text("mine\n")
        gitops.checked("add", "unrelated.txt", cwd=root)
    elif reason == "branch":
        gitops.checked("checkout", "-qb", "elsewhere", cwd=root)
    else:
        gitops.checked("checkout", "-q", "--detach", cwd=root)
    before = gitops.head(root)
    with pytest.raises(gitops.Busy) as error:
        gitops.merge(root, connection, job["id"])
    assert error.value.exit_code == 3
    assert gitops.head(root) == before
    state = pipeline.load(connection, job["id"])[0]
    assert (state["state"], state["pause_reason"]) == ("needs_human", "busy_merge")
    assert Path(job["worktree"]).exists() and gitops.branch_exists(root, job["branch"])


def test_conflict_is_aborted_and_preserves_unrelated_edits(repo):
    root, connection, _, _ = repo
    job = add_job(repo)
    (root / "app.txt").write_text("one\nfrom main\n")
    (root / "notes.txt").write_text("wip\n")
    gitops.checked("add", ".", cwd=root)
    gitops.checked("commit", "-qm", "main moves", cwd=root)
    (root / "notes.txt").write_text("wip, edited\n")
    before = gitops.head(root)
    with pytest.raises(gitops.GitError, match="undone"):
        gitops.merge(root, connection, job["id"])
    assert gitops.git("rev-parse", "--verify", "MERGE_HEAD", cwd=root).returncode != 0
    assert gitops.head(root) == before
    assert (root / "notes.txt").read_text() == "wip, edited\n"
    assert pipeline.load(connection, job["id"])[0]["state"] == "needs_human"


def test_changed_approval_head_is_rejected(repo):
    root, connection, _, _ = repo
    job = add_job(repo)
    gitops.checked("commit", "-q", "--allow-empty", "-m", "unreviewed", cwd=job["worktree"])
    with pytest.raises(gitops.GitError, match="changed after approval"):
        gitops.merge(root, connection, job["id"])
    assert "from the job" not in (root / "app.txt").read_text()


def test_only_completed_jobs_merge(repo):
    root, connection, _, _ = repo
    job = add_job(repo, state="running")
    with pytest.raises(gitops.GitError, match="only an approved"):
        gitops.merge(root, connection, job["id"])


def test_delete_removes_files_lease_and_cascades_rows(repo):
    root, connection, machine, repo_id = repo
    job = add_job(repo, state="needs_human")
    ports.lease(machine, repo_id, job["id"], ())
    message = gitops.delete(root, connection, job["id"], machine=machine, repo_id=repo_id)
    assert "worktree, branch, job folder" in message
    assert not Path(job["worktree"]).exists()
    assert not (root / "factory/jobs" / job["id"]).exists()
    for table in ("jobs", "executions", "attempts"):
        assert not connection.execute(f"SELECT * FROM {table}").fetchall()
    events = connection.execute("SELECT job_id,type,payload FROM events").fetchall()
    assert len(events) == 1
    assert events[0]["job_id"] is None
    assert events[0]["type"] == "job.state"
    assert json.loads(events[0]["payload"]) == {"deleted": job["id"]}
    assert not machine.execute("SELECT * FROM port_leases").fetchall()


def test_delete_requires_force_for_live_job(repo):
    root, connection, _, _ = repo
    job = add_job(repo, state="running")
    with pytest.raises(gitops.GitError, match="still running"):
        gitops.delete(root, connection, job["id"])
    gitops.delete(root, connection, job["id"], force=True)
    assert not Path(job["worktree"]).exists()


@pytest.mark.parametrize("action", ["delete", "merge"])
def test_cleanup_stops_owned_tree_but_foreign_listener_survives(repo, action):
    root, connection, _, _ = repo
    job = add_job(repo, state="running" if action == "delete" else "approved")
    context = context_for(repo, job)
    context.directory.mkdir(parents=True)
    token = "test-delete-owned-wrapper"
    # The fake CLI's child listener holds its cwd in the worktree on Windows.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    foreign = socket.socket()
    foreign.bind(("127.0.0.1", 0))
    foreign.listen()
    run_cli.atomic_json(context.directory / "scenario.json", {
        "sleep": 60,
        "spawn_child_listening_on_port": True,
        "server_port": port
    })
    fake = Path(__file__).with_name("fake_cli.py").resolve()
    run_cli.atomic_json(
        context.directory / "command.json", {
            "argv": [
                sys._base_executable, "-I", "-S",
                str(fake), "--scenario",
                str(context.directory / "scenario.json")
            ],
            "cwd":
            job["worktree"],
            "env": {},
            "stdin":
            None
        })
    with db.tx(connection):
        connection.execute(
            "INSERT INTO attempts (execution_id,n,kind,state,owner_token,dir) VALUES (?,1,'fresh','running',?,?)",
            (context.execution["id"], token, context.attempt["dir"]))
    wrapper = winjob.spawn_wrapper(context.directory, token)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if any(listener.port == port for listener in ports.listeners(range(port, port + 1))):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("fake child listener did not start")
        if action == "delete":
            gitops.delete(root, connection, job["id"], force=True)
        else:
            gitops.merge(root, connection, job["id"])
        wrapper.wait(timeout=15)
        assert not ports.listeners(range(port, port + 1))
        assert foreign.getsockname()[1] > 0
        with socket.create_connection(foreign.getsockname(), timeout=1):
            pass
    finally:
        foreign.close()
        if wrapper.poll() is None:
            members = winjob.owned_tree(context.directory, token)
            winjob.signal_tree(context.directory, token, members, kill=True)
            wrapper.wait(timeout=15)


def test_remove_refuses_a_path_outside_expected_worktree(repo):
    root, connection, _, _ = repo
    job = add_job(repo)
    with db.tx(connection):
        connection.execute("UPDATE jobs SET worktree=?", (str(root), ))
    with pytest.raises(gitops.GitError, match="outside"):
        gitops.delete(root, connection, job["id"])
    assert (root / "app.txt").exists()


def test_windows_removal_retries_file_locks(repo, monkeypatch):
    root, _, _, _ = repo
    job = add_job(repo)
    original = gitops.git
    calls = []

    def locked(*args, cwd):
        if args[:2] == ("worktree", "remove"):
            calls.append(args)
            if len(calls) < 6:
                return subprocess.CompletedProcess(args, 1, "", "file is locked")
        return original(*args, cwd=cwd)

    monkeypatch.setattr(gitops, "git", locked)
    # Patch the module reference, not the global os.name used by pathlib.
    monkeypatch.setattr(gitops, "os", type("OS", (), {"name": "nt"}))
    sleeps = []
    monkeypatch.setattr(gitops.time, "sleep", sleeps.append)
    assert gitops.remove_worktree(root, job)
    assert len(calls) == 6 and sleeps == [1] * 5


@pytest.mark.parametrize("type_,result", [("review", "PASS"), ("approval", "APPROVE"),
                                          ("task", "READY")])
def test_git_report_validation_requires_clean_worktree(repo, type_, result):
    job = add_job(repo)
    execution = {"type": type_, "uses": "custom", "head_before": gitops.head(job["worktree"])}
    (Path(job["worktree"]) / "untracked.txt").write_text("partial work")
    with pytest.raises(reports.ReportError, match="uncommitted"):
        reports.validate_git(reports.Report(result, "", {}), job, execution)
    assert gitops.validate_result(job, {**execution, "type": "task"}, "BLOCKED")


@pytest.mark.parametrize("type_", ["review", "approval"])
def test_git_report_validation_requires_unchanged_head(repo, type_):
    job = add_job(repo)
    execution = {"type": type_, "uses": "custom", "head_before": job["base_head"]}
    with pytest.raises(reports.ReportError, match="branch changed"):
        reports.validate_git(reports.Report("PASS", "", {}), job, execution)


def test_builder_must_commit_since_base_and_be_on_its_branch(repo):
    job = add_job(repo, change=False)
    execution = {"type": "task", "uses": "builder", "head_before": job["base_head"]}
    with pytest.raises(gitops.GitError, match="at least one commit"):
        gitops.validate_result(job, execution, "READY")
    gitops.checked("commit", "-q", "--allow-empty", "-m", "builder commit", cwd=job["worktree"])
    assert gitops.validate_result(job, execution, "READY") != job["base_head"]
    gitops.checked("checkout", "-q", "--detach", cwd=job["worktree"])
    with pytest.raises(gitops.GitError, match="must be on"):
        gitops.validate_result(job, execution, "READY")


@pytest.mark.parametrize("human", [False, True])
def test_sensitive_merge_requires_explicit_human_confirmation(repo, human):
    root, connection, _, _ = repo
    job = add_job(repo)
    tree = Path(job["worktree"])
    (tree / "migrations").mkdir()
    (tree / "migrations/001.sql").write_text("SELECT 1;\n")
    gitops.checked("add", ".", cwd=tree)
    gitops.checked("commit", "-qm", "sensitive change", cwd=tree)
    current = gitops.head(tree)
    with db.tx(connection):
        connection.execute("UPDATE jobs SET state='running',approved_head=? WHERE id=?",
                           (current, job["id"]))
        connection.execute("UPDATE executions SET state='done',result='READY',head_after=?",
                           (current, ))
    # Complete the real approval so the guarded state can be accepted by a human.
    pipeline.advance_job(connection, job["id"], current_head=current)
    with db.tx(connection):
        connection.execute(
            "UPDATE executions SET state='done',result='APPROVE',head_after=? "
            "WHERE type='approval'", (current, ))
        connection.execute("UPDATE jobs SET state='approved',approved_head=? WHERE id=?",
                           (current, job["id"]))
    before = gitops.head(root)
    with pytest.raises(gitops.Sensitive, match="migrations/001.sql"):
        gitops.merge(root, connection, job["id"], human=human, confirm_sensitive=not human)
    job, _ = pipeline.load(connection, job["id"])
    assert (job["state"], job["pause_reason"]) == ("needs_human", "sensitive")
    assert gitops.head(root) == before and tree.exists()
    with pytest.raises(gitops.Sensitive):
        gitops.merge(root, connection, job["id"], human=True)
    gitops.merge(root, connection, job["id"], human=True, confirm_sensitive=True)
    assert pipeline.load(connection, job["id"])[0]["state"] == "merged"


@pytest.mark.parametrize("path,pattern,expected", [
    ("migrations/001.sql", "**/migrations/**", True),
    ("src/migrations/001.sql", "**/migrations/**", True),
    ("uv.lock", "**/*.lock", True),
    ("src/auth/key.py", "**/auth/**", True),
    (".github/workflows/ci.yml", ".github/**", True),
    ("src/package.json", "package.json", False),
    ("public/authors.py", "**/auth/**", False),
])
def test_sensitive_globs(path, pattern, expected):
    assert gitops._matches(path, pattern) is expected


def test_sensitive_deletions_and_renames(repo):
    root, connection, _, _ = repo
    (root / "auth").mkdir()
    (root / "auth/key.txt").write_text("secret\n")
    (root / "old.lock").write_text("lock\n")
    gitops.checked("add", ".", cwd=root)
    gitops.checked("commit", "-qm", "sensitive originals", cwd=root)
    job = add_job(repo)
    tree = Path(job["worktree"])
    gitops.checked("mv", "auth/key.txt", "public.txt", cwd=tree)
    gitops.checked("rm", "old.lock", cwd=tree)
    gitops.checked("commit", "-qm", "rename and delete", cwd=tree)
    job["approved_head"] = gitops.head(tree)
    assert gitops.sensitive_paths(root, job) == ["auth/key.txt", "old.lock"]
