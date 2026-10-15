"""Machine-wide leases, real listeners, stop ownership and timers (plan §16.2)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from agent_factory import run_cli
from agent_factory.engine import db, ports, winjob
from agent_factory.engine.config import save_machine_settings
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import Supervisor
from engine_harness import StubAdapter, create_repo
from test_engine_supervisor import rows, state, store
from test_run_cli import alive, port_released, read_json, wait_for


def free_range(blocks=2):
    """Find a real contiguous test range; release probes before the engine uses it."""
    for base in range(22000, 60000, 10):
        probes = []
        try:
            for port in range(base, base + blocks * 10):
                probe = socket.socket()
                probes.append(probe)
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
                probe.listen()
            return base
        except OSError:
            continue
        finally:
            for probe in probes:
                probe.close()
    raise AssertionError("No free test range")


def configure_range(scheduler, base, blocks=2):
    scheduler.machine.call(lambda connection: save_machine_settings(
        connection, {"ports": {"range": f"{base}-{base + 10 * blocks - 1}"}}))


def leases(scheduler):
    return scheduler.machine.call(lambda connection: [dict(row) for row in connection.execute(
        "SELECT * FROM port_leases ORDER BY base")])


def edit(scheduler, identifier, sql, args=()):
    def apply(connection):
        with db.tx(connection):
            connection.execute(sql, args)
    scheduler.repos[identifier][1].call(apply)


def poll_stopped(supervisor, scheduler, identifier, attempt_id):
    def check():
        supervisor.poll()
        return scheduler.context(identifier, attempt_id).attempt["state"] == "stopped"
    wait_for(check)


def members(context):
    ready = wait_for(lambda: read_json(context.directory / "ready.json"))
    server = wait_for(lambda: read_json(context.directory / "server.json"))
    processes = [psutil.Process(ready["proc"]["wrapper_pid"]), psutil.Process(ready["pid"]),
                 psutil.Process(ready["child_pid"]), psutil.Process(server["pid"])]
    return processes, server["port"]


def test_concurrent_leases_never_overlap_across_repos_and_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifiers = [create_repo(tmp_path / f"repo-{n}", jobs=3) for n in range(2)]
    scheduler = Scheduler()
    base = free_range(6)
    configure_range(scheduler, base, 6)
    # Independent machine connections exercise the actual SQLite serialization.
    def reserve(index):
        connection = db.connect(tmp_path / "machine/machine.db")
        try:
            return ports.lease(connection, identifiers[index // 3], f"{index % 3 + 1:03}-job", ())
        finally:
            connection.close()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            first = list(pool.map(reserve, range(6)))
            second = list(pool.map(reserve, range(6)))
        assert sorted(first) == list(range(base, base + 60, 10))
        assert first == second
        assert len(leases(scheduler)) == 6
        configure_range(scheduler, base + 5, 7)
        # A shifted range must not overlap an older block.
        seventh = scheduler.machine.call(lambda connection: ports.lease(connection, identifiers[0], "new", ()))
        assert seventh == base + 65
    finally:
        scheduler.close()


def test_real_foreign_listener_skips_block_and_records_lowest_free_base(store):
    _, identifier, scheduler, supervisor = store
    base = free_range()
    configure_range(scheduler, base)
    # start() already leased the original default range; simulate a new job's lease.
    scheduler.machine.call(lambda connection: ports.release(connection, identifier, "001-job"))
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", base + 7))
        foreign.listen()
        found = next(item for item in ports.listeners() if item.port == base + 7)
        assert found.pid == os.getpid()
        assert found.name == psutil.Process().name()
        supervisor.sync_ports()
        assert rows(scheduler, identifier, "jobs")[0]["port_base"] == base + 10
        assert leases(scheduler)[0]["base"] == base + 10
        assert not port_released(base + 7)


@pytest.mark.parametrize("terminal", ["merged", "cancelled", "deleted"])
def test_release_on_merge_cancel_delete_but_keep_needs_human_and_paused(store, terminal):
    _, identifier, scheduler, supervisor = store
    initial = leases(scheduler)
    assert len(initial) == 1
    for retained in ("needs_human", "paused", "approved"):
        edit(scheduler, identifier, "UPDATE jobs SET state=?", (retained,))
        supervisor.sync_ports()
        assert leases(scheduler) == initial
    if terminal == "deleted":
        edit(scheduler, identifier, "DELETE FROM jobs")
    else:
        edit(scheduler, identifier, "UPDATE jobs SET state=?", (terminal,))
    supervisor.sync_ports()
    assert leases(scheduler) == []
    if terminal != "deleted":
        assert rows(scheduler, identifier, "jobs")[0]["port_base"] is None
    supervisor.sync_ports()  # repeat after restart is harmless


def test_missing_repo_keeps_lease_and_deleted_job_is_reconciled_after_restart(store):
    root, identifier, scheduler, supervisor = store
    initial = leases(scheduler)
    moved = root.with_name("temporarily-missing")
    scheduler.repos.pop(identifier)[1].close()
    root.rename(moved)
    try:
        scheduler.refresh()
        supervisor.sync_ports()
        assert leases(scheduler) == initial
    finally:
        moved.rename(root)
    scheduler.refresh()
    edit(scheduler, identifier, "DELETE FROM jobs")
    supervisor.start()
    assert leases(scheduler) == []


def test_exhaustion_pauses_without_launch_and_explicit_resume_reuses_pending_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifier = create_repo(tmp_path / "repo", jobs=2)
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    base = free_range(1)
    configure_range(scheduler, base, 1)
    try:
        supervisor.start()
        jobs = rows(scheduler, identifier, "jobs")
        assert [(job["state"], job["pause_reason"]) for job in jobs] == [("running", None), ("paused", "ports")]
        assert "No free 10-port block" in jobs[1]["note"]
        assert rows(scheduler, identifier, "attempts") == []
        edit(scheduler, identifier, "UPDATE jobs SET state='cancelled' WHERE id='001-job'")
        supervisor.sync_ports()
        scheduler.resume(identifier, "002-job")
        supervisor.tick()
        assert rows(scheduler, identifier, "jobs")[1]["port_base"] == base
        wait_for(lambda: (tmp_path / "repo/invocations").exists())
        assert len(rows(scheduler, identifier, "attempts")) == 1
        wait_for(lambda: (scheduler.context(identifier, 1).directory / "exit.json").exists())
        supervisor.poll()
    finally:
        for child in supervisor._children.values():
            child.wait(timeout=15)
        scheduler.close()


def test_foreign_listener_in_existing_lease_pauses_with_owner_and_is_never_signalled(store):
    root, identifier, scheduler, supervisor = store
    base = free_range(1)
    scheduler.machine.call(lambda connection: ports.release(connection, identifier, "001-job"))
    configure_range(scheduler, base, 1)
    supervisor.sync_ports()
    script = Path(__file__).with_name("fake_cli.py").resolve()
    foreign = subprocess.Popen([sys._base_executable, "-I", "-S", str(script),
                                "--server", str(root / "foreign.json"), "--port", str(base + 3)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: read_json(root / "foreign.json"))
        supervisor.tick()
        job = rows(scheduler, identifier, "jobs")[0]
        assert state(scheduler, identifier) == ("paused", "ports")
        assert f"port {base + 3}" in job["note"]
        assert f"pid {foreign.pid}" in job["note"]
        assert psutil.Process(foreign.pid).name() in job["note"]
        assert rows(scheduler, identifier, "attempts") == []
        assert not (root / "invocations").exists()
        assert not port_released(base + 3)
        assert foreign.poll() is None
    finally:
        foreign.terminate()
        foreign.wait(timeout=15)


def test_restart_repairs_job_port_base_from_durable_machine_lease(store):
    _, identifier, scheduler, supervisor = store
    initial = leases(scheduler)
    edit(scheduler, identifier, "UPDATE jobs SET port_base=NULL")
    supervisor.start()
    assert leases(scheduler) == initial
    assert rows(scheduler, identifier, "jobs")[0]["port_base"] == initial[0]["base"]


def test_assignment_retains_task_ux_ui_slots_without_giving_code_review_ports():
    for role, kind, offset in [("builder", "task", 0), ("custom-task", "task", 0),
                               ("reviewer-ux", "review", 3), ("reviewer-ui", "review", 6)]:
        assert ports.assigned(5100, {"uses": role, "type": kind}) == tuple(range(5100 + offset, 5103 + offset))
    assert ports.assigned(5100, {"uses": "reviewer-code", "type": "review"}) == ()


def test_listener_inspection_includes_ipv6_unknown_owners_and_fails_closed(monkeypatch):
    monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: [
        SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5101), pid=None),
        SimpleNamespace(status=psutil.CONN_ESTABLISHED, laddr=SimpleNamespace(port=5102), pid=None)])
    found = ports.listeners()
    assert found == (ports.Listener(5101, None, "unknown process", None),)
    assert ports.foreign(found, 5100, []) == found[0]
    monkeypatch.setattr(psutil, "net_connections", Mock(side_effect=psutil.AccessDenied()))
    with pytest.raises(ports.PortsUnavailable, match="Cannot inspect"):
        ports.listeners()


@pytest.mark.parametrize("fallback", [False, True])
def test_stop_kills_grandchild_frees_port_and_preserves_foreign_process(store, fallback):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True, "spawn_child_listening_on_port": True, "ignore_sigterm": True})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    tree, port = members(claim)
    supervisor.stop_grace = 0.15 if fallback else 10
    if fallback:
        tree[0].suspend()  # wrapper cannot read its flag; supervisor must escalate
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        foreign_port = foreign.getsockname()[1]
        try:
            supervisor.stop(identifier, "001-job")
            assert state(scheduler, identifier) == ("paused", "process")
            poll_stopped(supervisor, scheduler, identifier, claim.attempt["id"])
            wait_for(lambda: all(not alive(p) for p in tree))
            wait_for(lambda: port_released(port))
            assert not port_released(foreign_port)
            attempt = rows(scheduler, identifier, "attempts")[0]
            assert attempt["state"] == "stopped" and attempt["error_class"] is None
            assert attempt["session_id"] == "fake-session"
            assert rows(scheduler, identifier, "executions")[0]["state"] == "stopped"
            assert leases(scheduler)  # Stop retains the job's lease
            assert not (claim.directory / "report.md").exists()
        finally:
            if alive(tree[0]):
                tree[0].resume()


def test_stopping_parallel_group_keeps_completed_result_and_resume_exact_session(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "repo"
    identifier = create_repo(root, parallel=2)
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    supervisor.start()
    try:
        first = scheduler.claim_next()
        supervisor.launch(first)
        wait_for(lambda: (first.directory / "exit.json").exists())
        supervisor.poll()
        assert rows(scheduler, identifier, "executions")[0]["state"] == "done"
        run_cli.atomic_json(root / "scenario.json", {"hang": True})
        second = scheduler.claim_next()
        supervisor.launch(second)
        wait_for(lambda: (second.directory / "ready.json").exists())
        supervisor.poll()
        supervisor.stop(identifier, "001-job", attempt_id=second.attempt["id"])
        poll_stopped(supervisor, scheduler, identifier, second.attempt["id"])
        assert [e["state"] for e in rows(scheduler, identifier, "executions")] == ["done", "stopped"]
        scheduler.resume(identifier, "001-job")
        resumed = scheduler.claim_next()
        assert resumed.execution["id"] == second.execution["id"]
        assert resumed.attempt["kind"] == "resume" and resumed.attempt["session_id"] == "fake-session"
        assert rows(scheduler, identifier, "executions")[0]["state"] == "done"
    finally:
        for child in supervisor._children.values():
            child.wait(timeout=15)
        scheduler.close()


@pytest.mark.parametrize("override,restart", [(False, False), (True, False), (True, True)])
def test_timeout_uses_frozen_default_or_execution_override_and_survives_restart(store, override, restart):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True, "spawn_child_listening_on_port": True})
    frozen = json.loads(rows(scheduler, identifier, "jobs")[0]["frozen"])
    frozen["timeouts"]["executionMinutes"] = 60 if override else 1.0
    if override:
        frozen["pipeline"][0]["executions"][0]["timeoutMinutes"] = 1.0
    edit(scheduler, identifier, "UPDATE jobs SET frozen=?", (json.dumps(frozen),))
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    tree, port = members(claim)
    supervisor.poll()
    # Advance the durable launch timestamp instead of sleeping through a timer.
    launched = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    supervisor._update(claim, {"t_launch": launched})
    if restart:
        supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
        supervisor.start()
    poll_stopped(supervisor, scheduler, identifier, claim.attempt["id"])
    assert state(scheduler, identifier) == ("paused", "timeout")
    attempt = rows(scheduler, identifier, "attempts")[0]
    assert attempt["state"] == "stopped" and attempt["error_class"] == "timeout"
    assert "time limit" in attempt["error_detail"]
    wait_for(lambda: all(not alive(p) for p in tree))
    assert port_released(port)
    assert len(list((root / "invocations").iterdir())) == 1


def test_stop_intent_recovered_even_if_engine_dies_before_writing_stop_flag(store, monkeypatch):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    # Crash immediately after the durable intent transaction.
    monkeypatch.setattr(supervisor, "_stop_attempt", Mock(side_effect=RuntimeError("engine crashed")))
    with pytest.raises(RuntimeError, match="engine crashed"):
        supervisor.stop(identifier, "001-job")
    assert not (claim.directory / "stop").exists()
    with pytest.raises(ValueError, match="stopping"):
        scheduler.resume(identifier, "001-job")
    restarted = Supervisor(scheduler, {"codex": StubAdapter()})
    restarted.start()
    poll_stopped(restarted, scheduler, identifier, claim.attempt["id"])
    assert state(scheduler, identifier) == ("paused", "process")
    assert len(list((root / "invocations").iterdir())) == 1


def test_mismatched_wrapper_identity_never_allows_a_foreign_signal(store):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    claim.directory.mkdir(parents=True)
    process = psutil.Process()
    run_cli.atomic_json(claim.directory / "proc.json", {
        "wrapper_pid": process.pid, "wrapper_create_time": process.create_time(), "token": claim.attempt["owner_token"]})
    supervisor.stop(identifier, "001-job")
    assert process.is_running()
    assert not (claim.directory / "stop").exists()
    assert rows(scheduler, identifier, "attempts")[0]["state"] == "stopped"


def test_completed_exit_racing_with_stop_is_not_classified_as_a_process_crash(store):
    _, identifier, scheduler, supervisor = store
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "exit.json").exists())
    supervisor.stop(identifier, "001-job")
    poll_stopped(supervisor, scheduler, identifier, claim.attempt["id"])
    attempt = rows(scheduler, identifier, "attempts")[0]
    assert attempt["state"] == "stopped" and attempt["error_class"] is None
    assert attempt["exit_code"] == 0
    supervisor.tick()
    assert state(scheduler, identifier) == ("paused", "process")
    assert len(rows(scheduler, identifier, "attempts")) == 1


def test_long_execution_override_wins_over_short_default(store):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    frozen = json.loads(rows(scheduler, identifier, "jobs")[0]["frozen"])
    frozen["timeouts"]["executionMinutes"] = 0.00001
    frozen["pipeline"][0]["executions"][0]["timeoutMinutes"] = 60
    edit(scheduler, identifier, "UPDATE jobs SET frozen=?", (json.dumps(frozen),))
    claim = scheduler.claim_next()
    supervisor.launch(claim)
    wait_for(lambda: (claim.directory / "ready.json").exists())
    supervisor.poll()
    assert state(scheduler, identifier) == ("running", None)
    supervisor.stop(identifier, "001-job")
    poll_stopped(supervisor, scheduler, identifier, claim.attempt["id"])


def test_fresh_launch_time_uses_engine_clock_instead_of_kernel_boot_time(store, monkeypatch):
    root, identifier, scheduler, supervisor = store
    run_cli.atomic_json(root / "scenario.json", {"hang": True})
    claim = scheduler.claim_next()
    recorded = supervisor._record
    def skewed(context, process):
        # Keep the real ownership checks, but simulate the skewed kernel clock
        # seen under WSL when recording launch time for the timer.
        proxy = Mock(wraps=process)
        proxy.pid = process.pid
        proxy.create_time.return_value = process.create_time() - 30
        recorded(context, proxy)
    monkeypatch.setattr(supervisor, "_record", skewed)
    before = datetime.now(timezone.utc)
    supervisor.launch(claim)
    after = datetime.now(timezone.utc)
    attempt = scheduler.context(identifier, claim.attempt["id"]).attempt
    assert before <= datetime.fromisoformat(attempt["t_launch"]) <= after
    supervisor.stop(identifier, "001-job")
    poll_stopped(supervisor, scheduler, identifier, claim.attempt["id"])


@pytest.mark.parametrize("mode", ["tree", "orphan", "session"])
def test_owned_listener_is_allowed_while_parallel_peer_starts_and_is_freed_on_stop(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    root = tmp_path / "repo"
    identifier = create_repo(root, parallel=2)
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    configure_range(scheduler, free_range(1), 1)
    supervisor.start()
    try:
        base = rows(scheduler, identifier, "jobs")[0]["port_base"]
        run_cli.atomic_json(root / "scenario.json", {"hang": True, "spawn_child_listening_on_port": True,
                                                   "server_port": base, "orphan_server": mode == "orphan",
                                                   "server_new_session": mode == "session"})
        first = scheduler.claim_next()
        supervisor.launch(first)
        if mode == "orphan":
            ready = wait_for(lambda: read_json(first.directory / "ready.json"))
            server = wait_for(lambda: read_json(first.directory / "server.json"))
            tree = [psutil.Process(ready["proc"]["wrapper_pid"]), psutil.Process(ready["pid"]), psutil.Process(server["pid"])]
            wait_for(lambda: not psutil.pid_exists(ready["child_pid"]) if os.name == "nt"
                     else psutil.Process(server["pid"]).ppid() != ready["child_pid"])
            port = server["port"]
        else:
            tree, port = members(first)
        assert port == base
        run_cli.atomic_json(root / "scenario.json", {"sleep": 0.1})
        second = scheduler.claim_next()
        supervisor.launch(second)
        wait_for(lambda: (second.directory / "exit.json").exists())
        supervisor.poll()
        assert state(scheduler, identifier) == ("running", None)
        assert rows(scheduler, identifier, "executions")[1]["state"] == "done"
        supervisor.stop(identifier, "001-job")
        poll_stopped(supervisor, scheduler, identifier, first.attempt["id"])
        wait_for(lambda: port_released(port))
        assert all(not alive(p) for p in tree)
    finally:
        for attempt in rows(scheduler, identifier, "attempts"):
            context = scheduler.context(identifier, attempt["id"])
            owned = winjob.owned_tree(context.directory, attempt["owner_token"])
            winjob.signal_tree(context.directory, attempt["owner_token"], owned, kill=True)
        for child in supervisor._children.values():
            child.wait(timeout=15)
        scheduler.close()
