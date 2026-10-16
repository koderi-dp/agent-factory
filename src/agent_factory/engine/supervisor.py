"""Attempt launch/recovery, durable logs, port leases, Stop and timeouts (§6.4, §9).

Adapters are injected; no provider executable or provider module is required.
TODO (plan §3.1, session 1.9): service timer/wake-ups call tick() and own its lifetime.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from typing import Any, TypeVar

import psutil

from agent_factory import run_cli
from agent_factory.engine import db, pipeline, ports, winjob
from agent_factory.engine.adapters.base import (
    Adapter, EventUpdate, ExitOutcome, Prepared, UnsupportedProviderError, add_usage,
)
from agent_factory.engine.crash import crash_at
from agent_factory.engine.config import load_machine_settings
from agent_factory.engine.scheduler import Claim, LIVE, Scheduler

T = TypeVar("T")
STOP_DETAIL = "Stopped by user"


@dataclass
class Stopping:
    deadline: float
    members: list[psutil.Process]
    timeout: bool
    signalled: bool = False


def _read_with_retry(read: Callable[[], T]) -> T:
    # A sharing violation on a valid, atomically published Windows file is
    # temporary unavailability, NOT corrupt process/exit evidence. Never turn
    # it into a lost attempt. Exhausted retries propagate, leaving the live row
    # intact for the next recovery rather than authorizing any replacement.
    deadline = time.monotonic() + 2
    while True:
        try:
            return read()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_read_with_retry(lambda: path.read_text(encoding="utf-8")))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, ValueError):
        return None


def _token_processes(token: str) -> list[psutil.Process]:
    """Fallback for the small spawn-before-proc.json publication window."""
    matches = []
    for process in psutil.process_iter():
        try:
            argv = process.cmdline()
            if (any(a == "--token" and b == token for a, b in zip(argv, argv[1:]))
                    and process.status() != psutil.STATUS_ZOMBIE):
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


class Supervisor:
    def __init__(self, scheduler: Scheduler, adapters: Mapping[str, Adapter], *,
                 adoption_timeout: float = 10, stop_grace: float = 10) -> None:
        self.scheduler = scheduler
        self.adapters = dict(adapters)
        self.adoption_timeout = adoption_timeout
        self.stop_grace = stop_grace
        self._children: dict[tuple[str, int], subprocess.Popen] = {}
        self._spawn_times: dict[tuple[str, int], str] = {}
        self._stopping: dict[tuple[str, int], Stopping] = {}
        self._lock = threading.RLock()

    def adapter(self, provider: str) -> Adapter:
        try:
            return self.adapters[provider]
        except KeyError:
            raise UnsupportedProviderError(f"Unsupported provider: {provider}; no adapter is installed") from None

    def start(self) -> None:
        """Recover ALL repos before enabling even the first scheduling decision."""
        with self._lock, self.scheduler._lock:
            self.scheduler.ready = False
            self.scheduler.refresh()
            for identifier, (_, writer) in self.scheduler.repos.items():
                self._prune_events(writer)
                attempts = writer.call(lambda connection: [row[0] for row in connection.execute(
                    f"SELECT id FROM attempts WHERE state IN {LIVE} ORDER BY id")])
                for attempt_id in attempts:
                    self.recover(self.scheduler.context(identifier, attempt_id))
            self.scheduler.recount()
            self.sync_ports()
            self.scheduler.ready = True

    def _pause_ports(self, repo_id: str, job_id: str, detail: str) -> None:
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                changed = connection.execute(
                    "UPDATE jobs SET state='paused',pause_reason='ports',note=?,updated_at=? "
                    "WHERE id=? AND state='running'", (detail, pipeline._now(), job_id)).rowcount
                if changed:
                    pipeline._event(connection, job_id, pipeline._now(), "job.state",
                                    {"state": "paused", "pause_reason": "ports", "note": detail})
        self.scheduler.repos[repo_id][1].call(apply)

    def _range_listeners(self) -> tuple[ports.Listener, ...]:
        configured = self.scheduler.machine.call(lambda connection: load_machine_settings(connection).ports.range)
        start, end = map(int, configured.split("-"))
        return ports.listeners(range(start, end + 1))

    def _lease_job(self, repo_id: str, job_id: str, occupied: tuple[ports.Listener, ...]) -> int:
        base = self.scheduler.machine.call(lambda connection: ports.lease(connection, repo_id, job_id, occupied))
        def record(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                connection.execute("UPDATE jobs SET port_base=? WHERE id=?", (base, job_id))
        self.scheduler.repos[repo_id][1].call(record)
        return base

    def sync_ports(self) -> None:
        """Reconcile the two DBs after crashes; retain leases for missing repos.

        TODO (plan §11, session 1.8): call after git merge/delete job mutations.
        The service also calls it on every tick.
        """
        leases = self.scheduler.machine.call(lambda connection: [dict(row) for row in connection.execute(
            "SELECT * FROM port_leases")])
        occupied = None
        for identifier, (_, writer) in self.scheduler.repos.items():
            jobs = writer.call(lambda connection: [dict(row) for row in connection.execute("SELECT * FROM jobs")])
            by_id = {job["id"]: job for job in jobs}
            for lease in leases:
                if lease["repo_id"] != identifier:
                    continue
                job = by_id.get(lease["job_id"])
                if job is None or job["state"] in ports.RELEASE_STATES:
                    self.scheduler.machine.call(lambda connection, job_id=lease["job_id"]:
                                                ports.release(connection, identifier, job_id))
            for job in jobs:
                if job["state"] in ports.RELEASE_STATES:
                    def clear(connection: sqlite3.Connection, job_id: str = job["id"]) -> None:
                        with db.tx(connection):
                            connection.execute("UPDATE jobs SET port_base=NULL WHERE id=?", (job_id,))
                    writer.call(clear)
                elif job["state"] == "running":
                    try:
                        existing = next((lease for lease in leases if lease["repo_id"] == identifier
                                         and lease["job_id"] == job["id"]), None)
                        if existing is None and occupied is None:
                            occupied = self._range_listeners()
                        self._lease_job(identifier, job["id"], occupied or ())
                    except ports.PortsUnavailable as error:
                        self._pause_ports(identifier, job["id"], str(error))

    def _check_ports(self, context: Claim) -> None:
        if context.job["port_base"] is None:
            return
        writer = self.scheduler.repos[context.repo_id][1]
        ids = writer.call(lambda connection: [row[0] for row in connection.execute(
            f"SELECT a.id FROM attempts a JOIN executions e ON e.id=a.execution_id "
            f"WHERE e.job_id=? AND a.state IN {LIVE}", (context.job["id"],))])
        base = context.job["port_base"]
        occupied = ports.listeners(range(base, base + ports.BLOCK_SIZE))
        owned, contexts = [], []
        for attempt_id in ids:
            other = self.scheduler.context(context.repo_id, attempt_id)
            contexts.append(other)
            owned.extend(winjob.owned_tree(other.directory, other.attempt["owner_token"]))
        for listener in occupied:
            if listener.pid is None or listener.create_time is None:
                continue
            try:
                candidate = psutil.Process(listener.pid)
                if candidate.create_time() == listener.create_time and any(
                        winjob.contains_process(other.directory, other.attempt["owner_token"], candidate) for other in contexts):
                    owned.append(candidate)
            except psutil.Error:
                continue
        conflict = ports.foreign(occupied, base, owned)
        if conflict:
            raise ports.PortsUnavailable(conflict.detail)

    @staticmethod
    def _prune_events(writer: db.Writer) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        def prune(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                connection.execute("DELETE FROM events WHERE ts<? OR id <= "
                                   "(SELECT coalesce(max(id),0)-100000 FROM events)", (cutoff,))
        writer.call(prune)

    def tick(self) -> list[Claim]:
        """Poll then fill available slots, one repo per turn; no long-lived tx."""
        with self._lock:
            self.scheduler.refresh()
            if not self.scheduler.ready:
                self.start()
            self.poll()
            self.sync_ports()
            launched = []
            while (context := self.scheduler.claim_next()) is not None:
                self.launch(context)
                launched.append(context)
            return launched

    def launch(self, context: Claim) -> None:
        directory, token = context.directory, context.attempt["owner_token"]
        try:
            base = self._lease_job(context.repo_id, context.job["id"], self._range_listeners())
            context = replace(context, job={**context.job, "port_base": base})
            self._check_ports(context)
        except ports.PortsUnavailable as error:
            self._unclaim(context)
            self._pause_ports(context.repo_id, context.job["id"], str(error))
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            prepared = self.adapter(context.execution["provider"]).prepare(context)
            run_cli.atomic_write(directory / "prompt.md", prepared.prompt.encode("utf-8"))
            run_cli.atomic_write(directory / "system.md", prepared.system.encode("utf-8"))
            run_cli.atomic_json(directory / "command.json", dict(prepared.command))
            run_cli.atomic_json(directory / "packet.json", dict(prepared.packet))
            self._update(context, {"packet_bytes": prepared.packet_bytes if prepared.packet_bytes is not None
                                  else len(prepared.prompt.encode("utf-8"))})
            crash_at("after_prepare")
            spawned_at = pipeline._now()
            child = winjob.spawn_wrapper(directory, token)
            key = context.repo_id, context.attempt["id"]
            self._children[key] = child
            self._spawn_times[key] = spawned_at
        except Exception as error:
            self._fail_unspawned(context, str(error))
            return
        crash_at("after_spawn")
        # No transaction is held while waiting for proc.json publication.
        deadline = time.monotonic() + self.adoption_timeout
        while not (directory / "proc.json").exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        crash_at("before_record")
        self.recover(context)

    def _update(self, context: Claim, updates: Mapping[str, Any], *, expected_offset: int | None = None) -> None:
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                condition = " AND log_offset=?" if expected_offset is not None else ""
                extra = (expected_offset,) if expected_offset is not None else ()
                connection.execute(f"UPDATE attempts SET {','.join(key + '=?' for key in updates)} "
                                   f"WHERE id=? AND owner_token=? AND state IN {LIVE}{condition}",
                                   (*updates.values(), context.attempt["id"], context.attempt["owner_token"], *extra))
        self.scheduler.repos[context.repo_id][1].call(apply)

    def _record(self, context: Claim, process: psutil.Process) -> None:
        if context.attempt["state"] == "running":
            return
        pid, created = process.pid, process.create_time()
        # Prefer the engine's wall clock at spawn. Linux's boot-time based
        # create_time can lag wall time by a fraction of a second (notably WSL),
        # which would expire short timeouts before the CLI even starts.
        # After a crash, creation/queue times provide durable lower bounds.
        launched = self._spawn_times.get((context.repo_id, context.attempt["id"])) or max(
            datetime.fromtimestamp(created, timezone.utc).isoformat(), context.attempt["t_queued"])
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                changed = connection.execute(
                    "UPDATE attempts SET state='running',wrapper_pid=?,wrapper_ctime=?,t_launch=coalesce(t_launch,?) "
                    f"WHERE id=? AND owner_token=? AND state IN {LIVE}",
                    (pid, created, launched, context.attempt["id"], context.attempt["owner_token"])).rowcount
                if changed:
                    connection.execute("UPDATE executions SET state='running' WHERE id=? AND state IN ('claimed','running')",
                                       (context.execution["id"],))
                    pipeline._event(connection, context.job["id"], pipeline._now(), "attempt.state",
                                    {"id": context.attempt["id"], "state": "running"})
                    pipeline._event(connection, context.job["id"], pipeline._now(), "execution.state",
                                    {"id": context.execution["id"], "state": "running"})
        self.scheduler.repos[context.repo_id][1].call(apply)

    def recover(self, context: Claim) -> None:
        """The five §6.4 rows, plus conservative handling of incomplete evidence."""
        directory = context.directory
        # Even a lost wrapper may have published its provider session before
        # dying. Preserve that evidence so explicit Resume can use its exact ID.
        self.tail(context)
        context = self.scheduler.context(context.repo_id, context.attempt["id"])
        if ((directory / "stop").exists() or context.attempt["error_class"] == "timeout"
                or context.attempt["error_detail"] == STOP_DETAIL):
            self._stop_attempt(context, timeout=context.attempt["error_class"] == "timeout")
            return
        if (directory / "exit.json").exists():
            self.finalise(context)
            return
        process = winjob.owned_process(directory, context.attempt["owner_token"])
        if process is None and (directory / "proc.json").exists():
            # A failed identity read is not proof of death. Windows sharing /
            # process-inspection errors can be transient around publication or
            # exit. Retry briefly, retaining the exact same identity checks;
            # never adopt or signal a process on weaker evidence.
            deadline = time.monotonic() + min(0.25, self.adoption_timeout)
            while process is None and time.monotonic() < deadline:
                if (directory / "exit.json").exists():
                    self.finalise(context)
                    return
                time.sleep(0.02)
                process = winjob.owned_process(directory, context.attempt["owner_token"])
        if process is not None:
            try:
                self._record(context, process)
                context = self.scheduler.context(context.repo_id, context.attempt["id"])
                if self._expired(context):
                    self._stop_attempt(context, timeout=True)
            except psutil.Error:
                # Exit can race the ownership check; prefer durable exit evidence.
                if (directory / "exit.json").exists():
                    self.finalise(context)
                else:
                    self._lost(context)
            return
        if (directory / "proc.json").exists():
            # Recheck because a normal exit can appear during identity inspection.
            if (directory / "exit.json").exists():
                self.finalise(context)
            else:
                self._lost(context)
            return
        matches = _token_processes(context.attempt["owner_token"])
        if matches:
            deadline = time.monotonic() + self.adoption_timeout
            while time.monotonic() < deadline:
                if (directory / "proc.json").exists() or (directory / "exit.json").exists():
                    self.recover(context)
                    return
                time.sleep(0.02)
            # A live wrapper with no usable identity is never permission to relaunch.
            self._lost(context, "Wrapper did not publish proc.json within 10 s")
        elif context.attempt["t_launch"] is None and context.attempt["state"] == "launching":
            self._unclaim(context)
        else:
            self._lost(context)

    @staticmethod
    def _expired(context: Claim) -> bool:
        launched = context.attempt["t_launch"]
        if launched is None:
            return False
        frozen = context.job["frozen"]
        step = next(step for step in frozen["pipeline"] if step["id"] == context.execution["step_id"])
        entry = next(entry for entry in step["executions"] if entry["id"] == context.execution["exec_id"])
        minutes = entry.get("timeoutMinutes", frozen["timeouts"]["executionMinutes"])
        return (datetime.now(timezone.utc) - datetime.fromisoformat(launched)).total_seconds() >= minutes * 60

    def stop(self, repo_id: str, job_id: str, *, attempt_id: int | None = None) -> None:
        """Begin a nonblocking Stop. poll() completes escalation and persistence."""
        with self._lock:
            writer = self.scheduler.repos[repo_id][1]
            def pause(connection: sqlite3.Connection) -> list[int]:
                with db.tx(connection):
                    job, _ = pipeline.load(connection, job_id)
                    if job["state"] not in {"running", "paused"}:
                        raise ValueError("only running or paused jobs can be stopped")
                    ids = [row[0] for row in connection.execute(
                        f"SELECT a.id FROM attempts a JOIN executions e ON e.id=a.execution_id "
                        f"WHERE e.job_id=? AND a.state IN {LIVE}", (job_id,))]
                    if attempt_id is not None:
                        if attempt_id not in ids:
                            raise ValueError("attempt is not live in this job")
                        ids = [attempt_id]
                    now = pipeline._now()
                    connection.execute("UPDATE jobs SET state='paused',pause_reason='process',note=?,updated_at=? WHERE id=?",
                                       (STOP_DETAIL, now, job_id))
                    # Durable intent closes the tx-to-stop-file crash window.
                    connection.executemany("UPDATE attempts SET error_detail=? WHERE id=?", [(STOP_DETAIL, i) for i in ids])
                    pipeline._event(connection, job_id, now, "job.state", {"state": "paused", "pause_reason": "process"})
                    return ids
            for identifier in writer.call(pause):
                self._stop_attempt(self.scheduler.context(repo_id, identifier), timeout=False)

    def _stop_attempt(self, context: Claim, *, timeout: bool) -> None:
        key = context.repo_id, context.attempt["id"]
        if key not in self._stopping:
            members = winjob.owned_tree(context.directory, context.attempt["owner_token"])
            detail = "Execution time limit exceeded" if timeout else STOP_DETAIL
            def intent(connection: sqlite3.Connection) -> None:
                with db.tx(connection):
                    now = pipeline._now()
                    connection.execute(f"UPDATE attempts SET error_class=?,error_detail=? WHERE id=? AND state IN {LIVE}",
                                       ("timeout" if timeout else None, detail, context.attempt["id"]))
                    connection.execute("UPDATE jobs SET state='paused',pause_reason=?,note=?,updated_at=? "
                                       "WHERE id=? AND state IN ('running','paused')",
                                       ("timeout" if timeout else "process", detail, now, context.job["id"]))
                    pipeline._event(connection, context.job["id"], now, "job.state",
                                    {"state": "paused", "pause_reason": "timeout" if timeout else "process"})
            self.scheduler.repos[context.repo_id][1].call(intent)
            self._stopping[key] = Stopping(time.monotonic() + self.stop_grace, members, timeout)
        stopping = self._stopping[key]
        # Refresh the tree while its root is still verifiable, retaining earlier
        # identities if a descendant has since become orphaned.
        current = winjob.owned_tree(context.directory, context.attempt["owner_token"])
        known = {(p.pid, p.create_time()) for p in stopping.members}
        stopping.members.extend(p for p in current if (p.pid, p.create_time()) not in known)
        if not stopping.signalled and winjob.request_stop(context.directory, context.attempt["owner_token"]):
            stopping.signalled = True
            stopping.deadline = time.monotonic() + self.stop_grace
            if os.name != "nt":
                winjob.signal_tree(context.directory, context.attempt["owner_token"], stopping.members, kill=False)
        if any(winjob.alive(p) for p in stopping.members):
            if time.monotonic() >= stopping.deadline:
                winjob.signal_tree(context.directory, context.attempt["owner_token"], stopping.members, kill=True)
            return
        # A token-only wrapper can still be publishing proc.json. Leave it live
        # and retry rather than declaring Stop finished ahead of that process.
        if _token_processes(context.attempt["owner_token"]):
            return
        self.tail(context, final=True)
        info = _json(context.directory / "exit.json") or {}
        def finish(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                now = pipeline._now()
                changed = connection.execute(
                    "UPDATE attempts SET state='stopped',exit_code=?,t_exit=?,t_validated=? "
                    f"WHERE id=? AND owner_token=? AND state IN {LIVE}",
                    (info.get("exit_code"), info.get("ended_at", now), now,
                     context.attempt["id"], context.attempt["owner_token"])).rowcount
                if changed:
                    connection.execute("UPDATE executions SET state='stopped' WHERE id=? AND state IN ('claimed','running')",
                                       (context.execution["id"],))
                    pipeline._event(connection, context.job["id"], now, "attempt.state",
                                    {"id": context.attempt["id"], "state": "stopped", "error_class": "timeout" if stopping.timeout else None})
                    pipeline._event(connection, context.job["id"], now, "execution.state",
                                    {"id": context.execution["id"], "state": "stopped"})
        self.scheduler.repos[context.repo_id][1].call(finish)
        del self._stopping[key]

    def _unclaim(self, context: Claim) -> None:
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                removed = connection.execute("DELETE FROM attempts WHERE id=? AND owner_token=? "
                                             "AND state='launching' AND t_launch IS NULL",
                                             (context.attempt["id"], context.attempt["owner_token"])).rowcount
                if removed:
                    connection.execute("UPDATE executions SET state='pending' WHERE id=? AND state='claimed'",
                                       (context.execution["id"],))
                    pipeline._event(connection, context.job["id"], pipeline._now(), "execution.state",
                                    {"id": context.execution["id"], "state": "pending"})
        self.scheduler.repos[context.repo_id][1].call(apply)

    def _lost(self, context: Claim, detail: str = "Wrapper gone without exit.json") -> None:
        self._pause(context, "lost", "crash", "process", detail)

    def _fail_unspawned(self, context: Claim, detail: str) -> None:
        self._pause(context, "failed", "crash", "process", detail)

    def _pause(self, context: Claim, state: str, error: str, reason: str, detail: str) -> None:
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                now = pipeline._now()
                changed = connection.execute(
                    "UPDATE attempts SET state=?,error_class=?,error_detail=?,t_validated=? "
                    f"WHERE id=? AND owner_token=? AND state IN {LIVE}",
                    (state, error, detail, now, context.attempt["id"], context.attempt["owner_token"])).rowcount
                if changed:
                    connection.execute("UPDATE executions SET state='failed' WHERE id=? AND state IN ('claimed','running')",
                                       (context.execution["id"],))
                    connection.execute("UPDATE jobs SET state='paused',pause_reason=?,note=?,updated_at=? "
                                       "WHERE id=? AND state IN ('running','paused')", (reason, detail, now, context.job["id"]))
                    pipeline._event(connection, context.job["id"], now, "attempt.state",
                                    {"id": context.attempt["id"], "state": state, "error_class": error})
                    pipeline._event(connection, context.job["id"], now, "execution.state",
                                    {"id": context.execution["id"], "state": "failed"})
                    pipeline._event(connection, context.job["id"], now, "job.state", {"state": "paused", "pause_reason": reason})
        self.scheduler.repos[context.repo_id][1].call(apply)

    def tail(self, context: Claim, *, final: bool = False) -> None:
        path = context.directory / "events.jsonl"
        def read() -> bytes:
            with path.open("rb") as stream:
                stream.seek(context.attempt["log_offset"])
                # readlines' size hint keeps complete lines, even for a large
                # single tool-output event. Byte offsets also handle UTF-8/CRLF.
                return b"".join(stream.readlines(256 * 1024))
        try:
            data = _read_with_retry(read)
        except FileNotFoundError:
            return
        if not data:
            return
        end = len(data) if final else data.rfind(b"\n") + 1
        if not end:
            return  # leave partial UTF-8/JSON lines for the next poll
        updates: dict[str, Any] = {}
        now = pipeline._now()
        usage = json.loads(context.attempt["usage"]) if context.attempt.get("usage") else None
        last_activity = (context.attempt.get("last_activity_at") or context.attempt.get("t_first_event")
                         or context.attempt.get("t_launch") or context.attempt["t_queued"])
        quiet = (datetime.fromisoformat(now) - datetime.fromisoformat(last_activity)).total_seconds()
        for line in data[:end].splitlines():
            event = self.adapter(context.execution["provider"]).parse_event(line, context=context, quiet_seconds=quiet)
            if event.session_id is not None:
                updates["session_id"] = event.session_id
            if event.usage is not None:
                usage = add_usage(usage, event.usage)
                updates["usage"] = json.dumps(usage)
            if event.activity is not None:
                updates.update(last_activity=event.activity, last_activity_at=now)
                quiet = 0
        updates["log_offset"] = context.attempt["log_offset"] + end
        updates["t_first_event"] = context.attempt["t_first_event"] or now
        crash_at("during_tail")
        self._update(context, updates, expected_offset=context.attempt["log_offset"])

    def poll(self) -> None:
        for identifier, (_, writer) in self.scheduler.repos.items():
            identifiers = writer.call(lambda connection: [row[0] for row in connection.execute(
                f"SELECT id FROM attempts WHERE state IN {LIVE}")])
            for attempt_id in identifiers:
                self.recover(self.scheduler.context(identifier, attempt_id))
        for key, child in list(self._children.items()):
            if child.poll() is not None:
                del self._children[key]
                self._spawn_times.pop(key, None)
        self.scheduler.recount()

    def finalise(self, context: Claim) -> None:
        # exit.json precedes wrapper teardown. Do not advance into another step
        # until its containment has removed leftover servers.
        deadline = time.monotonic() + 0.25
        while winjob.owned_process(context.directory, context.attempt["owner_token"]) is not None:
            if time.monotonic() >= deadline:
                return
            time.sleep(0.01)
        info = _json(context.directory / "exit.json")
        if info is None or type(info.get("exit_code")) is not int or not isinstance(info.get("ended_at"), str):
            self._lost(context, "Invalid exit.json")
            return
        # Read every remaining byte before classification (including a final
        # non-newline stderr fragment). Offsets and parser effects commit together.
        while True:
            before = context.attempt["log_offset"]
            self.tail(context, final=True)
            context = self.scheduler.context(context.repo_id, context.attempt["id"])
            if context.attempt["log_offset"] == before:
                break
        # Fast CLI exits can precede the record tx entirely. Recover their launch
        # time from proc.json, without requiring the now-dead wrapper to exist.
        proc = _json(context.directory / "proc.json")
        if context.attempt["t_launch"] is None and proc and proc.get("token") == context.attempt["owner_token"]:
            created = proc.get("wrapper_create_time")
            if type(created) in (float, int) and math.isfinite(created):
                try:
                    launched = self._spawn_times.get((context.repo_id, context.attempt["id"])) or max(
                        datetime.fromtimestamp(created, timezone.utc).isoformat(), context.attempt["t_queued"])
                except (ValueError, OverflowError, OSError):
                    self._lost(context, "Invalid wrapper creation time")
                    return
                self._update(context, {"t_launch": launched, "wrapper_pid": proc.get("wrapper_pid"), "wrapper_ctime": created})
                context = self.scheduler.context(context.repo_id, context.attempt["id"])
        if context.attempt["t_launch"] is None:
            # §6.4 gives exit.json precedence even if process evidence was lost.
            # Queue time is the remaining durable lower bound for freshness.
            self._update(context, {"t_launch": context.attempt["t_queued"]})
            context = self.scheduler.context(context.repo_id, context.attempt["id"])
        outcome = self.adapter(context.execution["provider"]).classify(context, info)
        if outcome.state == "valid" and (outcome.completion is None or outcome.current_head is None):
            raise ValueError("valid exits require validated completion and current head")
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                now = pipeline._now()
                changed = connection.execute(
                    "UPDATE attempts SET state=?,exit_code=?,t_exit=?,t_validated=?,error_class=?,error_detail=? "
                    f"WHERE id=? AND owner_token=? AND state IN {LIVE}",
                    (outcome.state, info["exit_code"], info["ended_at"], now, outcome.error_class, outcome.detail,
                     context.attempt["id"], context.attempt["owner_token"])).rowcount
                if not changed:
                    return  # idempotent recovery, no duplicate completion/advance
                pipeline._event(connection, context.job["id"], now, "attempt.state",
                                {"id": context.attempt["id"], "state": outcome.state})
                job, executions = pipeline.load(connection, context.job["id"])
                execution = next(e for e in executions if e["id"] == context.execution["id"])
                if execution["state"] == "superseded":
                    return
                if outcome.state == "valid":
                    # Another parallel execution may already have paused the job.
                    # Retain this result without advancing that paused pipeline.
                    execution["state"] = "running"
                    if job["state"] == "running":
                        transition = pipeline.advance(job, executions, {execution["id"]: outcome.completion},
                                                      current_head=outcome.current_head)
                    else:
                        result = outcome.completion
                        transition = pipeline.Transition("wait", completed=({**execution, "state": "done",
                            "result": result.result, "head_after": result.head_after, "report_path": result.report_path},))
                    pipeline._apply(connection, job["id"], transition, now)
                    crash_at("before_advance_commit")
                elif outcome.state == "invalid":
                    retry = context.attempt["kind"] != "contract_retry"
                    connection.execute("UPDATE executions SET state=? WHERE id=?",
                                       ("pending" if retry else "failed", execution["id"]))
                    pipeline._event(connection, job["id"], now, "execution.state",
                                    {"id": execution["id"], "state": "pending" if retry else "failed"})
                    if not retry:
                        connection.execute("UPDATE jobs SET state='needs_human',pause_reason='contract',note=?,updated_at=? WHERE id=?",
                                           (outcome.detail, now, job["id"]))
                        pipeline._event(connection, job["id"], now, "job.state",
                                        {"state": "needs_human", "pause_reason": "contract"})
                else:
                    reason = "process" if outcome.error_class == "crash" else outcome.error_class
                    connection.execute("UPDATE executions SET state=? WHERE id=?",
                                       ("stopped" if outcome.state == "stopped" else "failed", execution["id"]))
                    connection.execute("UPDATE jobs SET state='paused',pause_reason=?,note=?,updated_at=? WHERE id=?",
                                       (reason, outcome.detail, now, job["id"]))
                    pipeline._event(connection, job["id"], now, "execution.state",
                                    {"id": execution["id"], "state": "stopped" if outcome.state == "stopped" else "failed"})
                    pipeline._event(connection, job["id"], now, "job.state", {"state": "paused", "pause_reason": reason})
        self.scheduler.repos[context.repo_id][1].call(apply)
