"""Real SQLite, wrappers and fake-CLI crash/recovery tests for plan §6.4/§16.3."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import psutil
import pytest

from agent_factory import run_cli
from agent_factory.engine import db, winjob
from agent_factory.engine.config import save_machine_settings
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import Supervisor, UnsupportedProviderError
from engine_harness import StubAdapter, create_repo
from test_run_cli import alive, wait_for

HARNESS = Path(__file__).with_name("engine_harness.py")
CRASH_POINTS = [
    "after_claim", "after_prepare", "after_spawn", "before_record", "during_tail",
    "before_advance_commit"
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine č"))
    root = tmp_path / "repo č"
    repo_id = create_repo(root)
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    supervisor.start()
    yield root, repo_id, scheduler, supervisor
    # Kill only wrappers whose durable owner token matches a test row.
    for identifier, (_, writer) in scheduler.repos.items():
        ids = writer.call(
            lambda connection: [r[0] for r in connection.execute("SELECT id FROM attempts")])
        for attempt_id in ids:
            context = scheduler.context(identifier, attempt_id)
            process = winjob.owned_process(context.directory, context.attempt["owner_token"])
            if process:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                wait_for(lambda: not alive(process))
    for child in supervisor._children.values():
        child.wait(timeout=15)
    scheduler.close()


def rows(scheduler, repo_id, table):
    return scheduler.repos[repo_id][1].call(
        lambda connection:
        [dict(r) for r in connection.execute(f"SELECT * FROM {table} ORDER BY id")])


def state(scheduler, repo_id):
    job = rows(scheduler, repo_id, "jobs")[0]
    return job["state"], job["pause_reason"]


def settle(supervisor, scheduler, repo_id):

    def check():
        supervisor.tick()
        return state(scheduler, repo_id)[0] != "running"

    wait_for(check)


def dead_proc(context):
    context.directory.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    run_cli.atomic_json(
        context.directory / "proc.json", {
            "wrapper_pid": process.pid,
            "wrapper_create_time": process.create_time() - 1,
            "token": context.attempt["owner_token"],
        })


def test_recovery_unspawned_claim_is_only_automatic_relaunch(store):
    root, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    assert scheduler.live_global == 1
    supervisor.start()
    assert rows(scheduler, identifier, "attempts") == []
    assert rows(scheduler, identifier, "executions")[0]["state"] == "pending"
    assert scheduler.live_global == 0
    assert not (root / "invocations").exists()
    settle(supervisor, scheduler, identifier)
    assert state(scheduler, identifier) == ("needs_human", "verify")
    assert len(list((root / "invocations").iterdir())) == 1


def test_recovery_living_process_adopts_and_continues_offset(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"sleep": 1})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    supervisor.poll()
    before = scheduler.context(identifier, claim.attempt["id"])
    assert before.attempt["log_offset"] > 0
    before_seen = len(supervisor.adapters["codex"].seen)
    supervisor.start()
    adopted = scheduler.context(identifier, claim.attempt["id"])
    assert adopted.attempt["state"] == "running"
    assert adopted.attempt["log_offset"] == before.attempt["log_offset"]
    assert scheduler.live_global == 1
    settle(supervisor, scheduler, identifier)
    assert len(supervisor.adapters["codex"].seen) == before_seen
    assert len(list((root / "invocations").iterdir())) == 1


def test_recovery_exit_exists_before_record_finalises_normally(store, monkeypatch):
    root, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    original = supervisor.recover
    monkeypatch.setattr(supervisor, "recover", lambda context: None)
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "exit.json").exists())
    assert rows(scheduler, identifier, "attempts")[0]["t_launch"] is None
    monkeypatch.setattr(supervisor, "recover", original)
    supervisor.start()
    attempt = rows(scheduler, identifier, "attempts")[0]
    assert attempt["state"] == "valid" and attempt["t_launch"]
    assert attempt["log_offset"] == (claim.directory / "events.jsonl").stat().st_size
    assert state(scheduler, identifier) == ("needs_human", "verify")
    history = rows(scheduler, identifier, "events")
    supervisor.start()
    assert rows(scheduler, identifier, "events") == history
    assert len(list((root / "invocations").iterdir())) == 1


def test_transient_exit_artifact_sharing_violation_does_not_lose_completed_attempt(
        store, monkeypatch):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    recover = supervisor.recover
    monkeypatch.setattr(supervisor, "recover", lambda context: None)
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "exit.json").exists())
    monkeypatch.setattr(supervisor, "recover", recover)
    original_read = Path.read_text
    reads = 0

    def transient_read(path, *args, **kwargs):
        nonlocal reads
        if path == claim.directory / "exit.json":
            reads += 1
            if reads == 1:
                raise PermissionError("Windows sharing violation")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", transient_read)
    supervisor.start()
    assert reads >= 2
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "valid"
    assert state(scheduler, identifier) == ("needs_human", "verify")


@pytest.mark.parametrize("has_proc,t_launch", [(True, None), (False, "2026-01-01T00:00:00+00:00")])
def test_recovery_dead_or_missing_identity_after_record_pauses_never_relaunches(
        store, has_proc, t_launch):
    root, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    if has_proc:
        dead_proc(claim)
    if t_launch:
        supervisor._update(claim, {"t_launch": t_launch, "state": "running"})
    supervisor.start()
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "lost"
    assert state(scheduler, identifier) == ("paused", "process")
    supervisor.tick()
    assert len(rows(scheduler, identifier, "attempts")) == 1
    assert not (root / "invocations").exists()


def test_recovery_token_scan_waits_for_proc_then_adopts_real_process(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    path = claim.directory / "proc.json"
    data = path.read_bytes()
    path.unlink()
    publisher = threading.Thread(
        target=lambda: (time.sleep(0.15), run_cli.atomic_write(path, data)))
    publisher.start()
    supervisor.start()
    publisher.join()
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "running"
    assert scheduler.live_global == 1
    assert len(list((root / "invocations").iterdir())) == 1


def test_recovery_token_process_without_proc_times_out_conservatively(store, monkeypatch):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    monkeypatch.setattr("agent_factory.engine.supervisor._token_processes",
                        lambda token: [psutil.Process()])
    supervisor.adoption_timeout = 0.03
    supervisor.start()
    assert state(scheduler, identifier) == ("paused", "process")
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "lost"


def test_recovery_real_wrapper_death_is_lost_without_duplicate_cli(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    process = winjob.owned_process(claim.directory, claim.attempt["owner_token"])
    if os.name == "nt":
        process.kill()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    supervisor._children[(identifier, claim.attempt["id"])].wait(timeout=15)
    supervisor.start()
    supervisor.start()
    assert state(scheduler, identifier) == ("paused", "process")
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "lost"
    assert rows(scheduler, identifier, "attempts")[0]["session_id"] == "fake-session"
    assert len(list((root / "invocations").iterdir())) == 1


def test_transient_ownership_inspection_failure_does_not_mark_live_wrapper_lost(store, monkeypatch):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    original = winjob.owned_process
    inspections = 0

    def transient(directory, token):
        nonlocal inspections
        inspections += 1
        return None if inspections == 1 else original(directory, token)

    monkeypatch.setattr(winjob, "owned_process", transient)
    supervisor.start()
    assert inspections >= 2
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "running"
    assert state(scheduler, identifier) == ("running", None)


@pytest.mark.parametrize("session_id", [None, "known-session"])
def test_lost_resume_requires_user_and_preserves_known_session_or_warns(store, session_id):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    supervisor._update(claim, {"session_id": session_id})
    dead_proc(claim)
    supervisor.start()
    assert scheduler.claim_next() is None
    scheduler.resume(identifier, claim.job["id"])
    resumed = scheduler.claim_next()
    assert resumed.attempt["kind"] == ("resume" if session_id else "fresh")
    assert resumed.attempt["session_id"] == session_id
    assert resumed.attempt["n"] == 2
    if session_id is None:
        resumed.directory.mkdir(parents=True)
        prepared = StubAdapter().prepare(resumed)
        assert "partial uncommitted work" in prepared.prompt


def test_tail_byte_offsets_crlf_partial_utf8_and_restart(store):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    claim.directory.mkdir(parents=True)
    path = claim.directory / "events.jsonl"
    first = b'{"session_id":"session-1"}\r\n'
    last = '{"activity":"č"}'.encode("utf-8")
    path.write_bytes(first + last[:-2])
    supervisor.tail(claim)
    updated = scheduler.context(identifier, claim.attempt["id"])
    assert updated.attempt["log_offset"] == len(first)
    assert updated.attempt["session_id"] == "session-1"
    with path.open("ab") as stream:
        stream.write(last[-2:] + b"\n")
    # New supervisor/adapter, as on service restart.
    replacement = Supervisor(scheduler, {"codex": StubAdapter()})
    replacement.tail(updated)
    updated = scheduler.context(identifier, claim.attempt["id"])
    assert updated.attempt["last_activity"] == "č"
    assert updated.attempt["log_offset"] == path.stat().st_size
    assert len(replacement.adapters["codex"].seen) == 1
    replacement.tail(updated)
    assert len(replacement.adapters["codex"].seen) == 1


def test_tail_large_event_final_fragment_and_stale_offset_never_moves_backwards(store):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    claim.directory.mkdir(parents=True)
    path = claim.directory / "events.jsonl"
    large = (json.dumps({"activity": "x" * (300 * 1024)}) + "\n").encode()
    fragment = b'{"usage":{"output":99}}'
    path.write_bytes(large + fragment)
    supervisor.tail(claim)
    updated = scheduler.context(identifier, claim.attempt["id"])
    assert updated.attempt["log_offset"] == len(large)
    supervisor.tail(updated, final=True)
    supervisor.tail(claim)  # stale reader cannot regress offset/parser effects
    updated = scheduler.context(identifier, claim.attempt["id"])
    assert updated.attempt["log_offset"] == path.stat().st_size
    assert json.loads(updated.attempt["usage"]) == {"output": 99}


@pytest.mark.parametrize("reports,expected", [(["invalid", "APPROVE\n"], "verify"),
                                              (["invalid", "invalid"], "contract")])
def test_contract_retry_is_once_and_fresh(store, reports, expected):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"reports": reports})
    settle(supervisor, scheduler, identifier)
    attempts = rows(scheduler, identifier, "attempts")
    assert [a["kind"] for a in attempts] == ["fresh", "contract_retry"]
    assert attempts[0]["state"] == "invalid"
    assert attempts[1]["state"] == ("valid" if expected == "verify" else "invalid")
    assert state(scheduler, identifier) == ("needs_human", expected)
    assert len(list((root / "invocations").iterdir())) == 2
    retry = scheduler.context(identifier, attempts[1]["id"])
    assert "invalid first line" in (retry.directory / "prompt.md").read_text()


def test_nonzero_exit_pauses_via_injected_classifier(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"exit": 7})
    settle(supervisor, scheduler, identifier)
    attempt = rows(scheduler, identifier, "attempts")[0]
    assert (attempt["state"], attempt["exit_code"], attempt["error_class"]) == ("failed", 7,
                                                                                "crash")
    assert state(scheduler, identifier) == ("paused", "process")


def test_unsupported_provider_is_clear_and_does_not_invoke_cli(store):
    root, identifier, scheduler, supervisor = store
    supervisor.adapters.clear()
    with pytest.raises(UnsupportedProviderError, match="Unsupported provider: codex"):
        supervisor.adapter("codex")
    supervisor.tick()
    assert state(scheduler, identifier) == ("paused", "process")
    assert "Unsupported provider: codex" in rows(scheduler, identifier,
                                                 "attempts")[0]["error_detail"]
    assert not (root / "invocations").exists()


def test_scheduling_is_disabled_until_recovery(store):
    _, _, scheduler, _ = store
    scheduler.ready = False
    with pytest.raises(RuntimeError, match="Recover every registered repo"):
        scheduler.claim_next()


def test_partial_unique_index_and_concurrent_claims(store):
    _, identifier, scheduler, _ = store
    competing = Scheduler()
    # Both services have completed startup before competing; actual service
    # single-instance lock is added in session 1.9.
    Supervisor(competing, {"codex": StubAdapter()}).start()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = list(
                pool.map(lambda n: (scheduler if n % 2 else competing).claim_next(), range(24)))
        claims = [claim for claim in claims if claim]
        assert len(claims) == 1
        assert len(rows(scheduler, identifier, "attempts")) == 1
        claim = claims[0]

        def duplicate(connection):
            with db.tx(connection):
                connection.execute(
                    "INSERT INTO attempts (execution_id,n,kind,state,owner_token,dir) VALUES (?,2,'fresh','running','other','other')",
                    (claim.execution["id"], ))

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            scheduler.repos[identifier][1].call(duplicate)
    finally:
        competing.close()


def test_limits_round_robin_and_restart_recount_two_repos_three_jobs_four_executions(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifiers = [
        create_repo(tmp_path / f"repo-{n}", jobs=3, parallel=4, per_job=2) for n in range(2)
    ]
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    supervisor.start()
    try:
        scheduler.machine.call(
            lambda connection: save_machine_settings(connection, {"concurrency": {
                "global": 7
            }}))
        # Windows wall-clock timestamps can tie. Observe successful dispatches
        # inside the scheduler's serialised claim path instead of sorting ties
        # by ThreadPoolExecutor input order (which is not lock acquisition order).
        dispatch_order = []
        original_claim = scheduler._claim

        def observed_claim(*args):
            claim = original_claim(*args)
            if claim is not None:
                dispatch_order.append(claim.repo_id)
            return claim

        monkeypatch.setattr(scheduler, "_claim", observed_claim)
        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = [
                claim for claim in pool.map(lambda _: scheduler.claim_next(), range(40)) if claim
            ]
        assert len(claims) == scheduler.live_global == 7
        assert dispatch_order == [identifiers[n % 2] for n in range(7)]
        counts = {}
        for claim in claims:
            key = claim.repo_id, claim.job["id"]
            counts[key] = counts.get(key, 0) + 1
        assert max(counts.values()) == 2
        assert all(
            len(rows(scheduler, identifier, "executions")) == 12 for identifier in identifiers)
        assert scheduler.recount() == 7
        # The engine recomputes capacity from every repo rather than trusting
        # a stale in-memory counter after a service restart/config change.
        scheduler.live_global = 0
        assert scheduler.claim_next() is None
        assert scheduler.live_global == 7
        scheduler.machine.call(
            lambda connection: save_machine_settings(connection, {"concurrency": {
                "global": 3
            }}))
        assert scheduler.claim_next() is None
        for claim in claims:
            supervisor._lost(claim)
        assert scheduler.recount() == 0
    finally:
        scheduler.close()


def test_live_limits_hold_through_two_repos_three_jobs_four_parallel_executions(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifiers = [
        create_repo(tmp_path / f"repo-{n}", jobs=3, parallel=4, per_job=2) for n in range(2)
    ]
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    supervisor.start()
    try:
        scheduler.machine.call(
            lambda connection: save_machine_settings(connection, {"concurrency": {
                "global": 7
            }}))
        maximum = 0
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            supervisor.tick()
            total = 0
            for identifier in identifiers:
                live = scheduler.repos[identifier][1].call(lambda connection: connection.execute(
                    "SELECT e.job_id,count(*) FROM attempts a JOIN executions e ON e.id=a.execution_id "
                    "WHERE a.state IN ('launching','running') GROUP BY e.job_id").fetchall())
                assert all(row[1] <= 2 for row in live)
                total += sum(row[1] for row in live)
            assert total <= 7
            maximum = max(maximum, total)
            if all(
                    all(row["state"] == "needs_human"
                        for row in rows(scheduler, identifier, "jobs"))
                    for identifier in identifiers):
                break
            time.sleep(0.02)
        else:
            pytest.fail("2 repos × 3 jobs × 4 reviews did not finish")
        assert maximum >= 2
        for identifier in identifiers:
            attempts = rows(scheduler, identifier, "attempts")
            assert len(attempts) == 15 and all(a["state"] == "valid" for a in attempts)
            root = scheduler.repos[identifier][0]
            assert len(list((root / "invocations").iterdir())) == 15
    finally:
        for child in supervisor._children.values():
            child.wait(timeout=15)
        scheduler.close()


def test_competing_schedulers_enforce_global_capacity_across_different_repo_databases(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifiers = [
        create_repo(tmp_path / f"repo-{n}", jobs=3, parallel=4, per_job=2) for n in range(2)
    ]
    schedulers = [Scheduler(), Scheduler()]
    for scheduler in schedulers:
        Supervisor(scheduler, {"codex": StubAdapter()}).start()
    try:
        schedulers[0].machine.call(
            lambda connection: save_machine_settings(connection, {"concurrency": {
                "global": 3
            }}))
        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = [
                claim for claim in pool.map(lambda n: schedulers[n % 2].claim_next(), range(30))
                if claim
            ]
        assert len(claims) == 3
        assert len({claim.execution["id"] for claim in claims}) <= 3
        assert schedulers[0].recount() == schedulers[1].recount() == 3
        assert sum(len(rows(schedulers[0], identifier, "attempts"))
                   for identifier in identifiers) == 3
    finally:
        for scheduler in schedulers:
            scheduler.close()


def run_service(crash=None):
    env = dict(os.environ)
    if crash:
        env["FACTORY_CRASH_AT"] = crash
    else:
        env.pop("FACTORY_CRASH_AT", None)
    return subprocess.run([sys.executable, str(HARNESS)],
                          env=env,
                          capture_output=True,
                          text=True,
                          timeout=35)


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_crash_injection_restarts_with_exactly_one_fake_invocation(store, point):
    root, identifier, scheduler, supervisor = store
    # Keep the process alive through spawn/record/tail crash windows.
    run_cli.atomic_json(root / "scenario.json", {"sleep": 0.4})
    crashed = run_service(point)
    assert crashed.returncode == 1, (point, crashed.stdout, crashed.stderr)
    assert f"FACTORY_CRASH_AT={point}" in crashed.stderr, crashed.stderr
    if point in {"after_claim", "after_prepare"}:
        assert not (root / "invocations").exists()
    else:
        wait_for(lambda: len(list((root / "invocations").glob("*.json"))) == 1)
    restarted = run_service()
    assert restarted.returncode == 0, (point, restarted.stdout, restarted.stderr)
    assert state(scheduler, identifier) == ("needs_human", "verify")
    attempts = rows(scheduler, identifier, "attempts")
    assert len(attempts) == 1 and attempts[0]["state"] == "valid"
    assert len(list((root / "invocations").iterdir())) == 1
    context = scheduler.context(identifier, attempts[0]["id"])
    assert attempts[0]["log_offset"] == (context.directory / "events.jsonl").stat().st_size
    assert json.loads(attempts[0]["usage"]) == {"input": 10, "output": 2}
    assert len([
        row for row in rows(scheduler, identifier, "events")
        if row["type"] == "execution.state" and json.loads(row["payload"]).get("state") == "done"
    ]) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper-only death")
def test_resume_blocks_surviving_cli_after_wrapper_only_death(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {
        "sleep": 60,
        "spawn_child_listening_on_port": True,
        "server_new_session": True
    })
    claim = supervisor.tick()[0]
    wait_for(lambda: (claim.directory / "server.json").exists())
    descendants = winjob.token_descendants(claim.attempt["owner_token"])
    assert len(descendants) >= 3
    wrapper = winjob.owned_process(claim.directory, claim.attempt["owner_token"])
    wrapper.kill()  # deliberately leave the CLI and its escaped server alive
    wait_for(lambda: not alive(wrapper))
    try:
        supervisor.start()
        assert state(scheduler, identifier) == ("paused", "process")
        assert scheduler.context(identifier, claim.attempt["id"]).attempt["state"] == "lost"
        with pytest.raises(ValueError, match="descendants"):
            scheduler.resume(identifier, claim.job["id"])
        assert scheduler.claim_next() is None
        assert len(list((root / "invocations").iterdir())) == 1
    finally:
        for process in descendants:
            if alive(process):
                process.kill()
        wait_for(lambda: not winjob.token_descendants(claim.attempt["owner_token"]))
    scheduler.resume(identifier, claim.job["id"])
    assert scheduler.claim_next() is not None


def test_rework_rejects_live_peer_without_superseding_it(tmp_path, monkeypatch):
    from agent_factory.engine import pipeline
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "repo"
    identifier = create_repo(root, parallel=2)
    scheduler = Scheduler()

    class UnevenAdapter(StubAdapter):

        def prepare(self, context):
            prepared = super().prepare(context)
            path = context.directory / "scenario.json"
            scenario = json.loads(path.read_text())
            scenario.update(sleep=0.1 if context.execution["exec_id"] == "review-0" else 60,
                            report="invalid")
            run_cli.atomic_json(path, scenario)
            return prepared

    supervisor = Supervisor(scheduler, {"codex": UnevenAdapter()})
    try:
        supervisor.start()
        launched = supervisor.tick()
        peer = next(c for c in launched if c.execution["exec_id"] == "review-1")
        wait_for(lambda: (peer.directory / "ready.json").exists())
        settle(supervisor, scheduler, identifier)
        assert state(scheduler, identifier) == ("needs_human", "contract")
        before = rows(scheduler, identifier, "executions")
        writer = scheduler.repos[identifier][1]
        with pytest.raises(pipeline.PipelineError, match="live attempts"):
            writer.call(lambda connection: pipeline.rework_job(
                connection, peer.job["id"], "Fix it", current_head="head"))
        assert rows(scheduler, identifier, "executions") == before
        assert winjob.owned_process(peer.directory, peer.attempt["owner_token"]) is not None
        assert supervisor.tick() == []
        assert len(list((root / "invocations").iterdir())) == 3
    finally:
        for child in supervisor._children.values():
            if child.poll() is None:
                psutil.Process(child.pid).kill()
            child.wait(timeout=15)
        scheduler.close()


@pytest.mark.parametrize("explicit", [False, True])
def test_doctor_persists_paths_and_running_adapter_launches_without_path(
        store, monkeypatch, explicit):
    from agent_factory.engine import scaffold
    from agent_factory.engine.adapters.codex import CodexAdapter
    from agent_factory.engine.config import load_machine_settings
    root, identifier, scheduler, supervisor = store
    if explicit:
        scheduler.machine.call(
            lambda connection: save_machine_settings(connection, {"cli": {
                "codex": sys.executable
            }}))
    monkeypatch.setattr(scaffold.shutil, "which", lambda name: sys._base_executable
                        if name == "codex" else None)
    checks = scaffold.doctor()
    assert next(c for c in checks if c["check"] == "codex")["ok"]
    settings = scheduler.machine.call(load_machine_settings)
    assert Path(settings.cli.codex).resolve() == Path(
        sys.executable if explicit else sys._base_executable).resolve()

    class RecordingAdapter(CodexAdapter):
        classify = StubAdapter.classify

        def prepare(self, context):
            assert self.settings.cli.codex == settings.cli.codex
            prepared = StubAdapter().prepare(context)
            prepared.command["argv"][0] = self.settings.cli.codex
            return prepared

    adapter = RecordingAdapter()  # created before discovering machine settings
    supervisor.adapters["codex"] = adapter
    monkeypatch.setenv("PATH", "")
    launched = supervisor.tick()
    assert len(launched) == 1
    wait_for(lambda: (launched[0].directory / "ready.json").exists())
    settle(supervisor, scheduler, identifier)
    assert state(scheduler, identifier) == ("needs_human", "verify")
