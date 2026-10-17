"""Machine-wide ten-port leases and listener inspection (plan §9).

Inspect processes before entering a DB transaction. A lease reserves numbers for
the engine; it cannot prevent another application from binding them later.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import sqlite3
from typing import Any

import psutil

from agent_factory.engine import db, pipeline
from agent_factory.engine.config import load_machine_settings

BLOCK_SIZE = 10
RELEASE_STATES = frozenset({"merged", "cancelled"})


class PortsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Listener:
    port: int
    pid: int | None
    name: str
    create_time: float | None

    @property
    def detail(self) -> str:
        return f"port {self.port} is held by {self.name} (pid {self.pid if self.pid is not None else 'unknown'})"


def _tcp_listeners() -> list[tuple[int, int | None]]:
    """(port, pid) for every TCP listener, tolerating macOS permissions."""
    try:
        connections = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        # macOS: psutil cannot inspect other users' processes, so the whole call
        # fails. Enumerate per process and skip the ones we cannot inspect.
        return _per_process_tcp_listeners()
    except psutil.Error as error:
        raise PortsUnavailable(f"Cannot inspect TCP listeners: {error}") from error
    result = []
    for connection in connections:
        if connection.status == psutil.CONN_LISTEN and connection.laddr:
            result.append((connection.laddr.port, connection.pid))
    return result


def _per_process_tcp_listeners() -> list[tuple[int, int | None]]:
    """macOS fallback: net_connections fails on the first inaccessible process,
    so enumerate per process and skip the ones we cannot inspect."""
    result = []
    for process in psutil.process_iter():
        try:
            for connection in process.connections(kind="tcp"):
                if connection.status == psutil.CONN_LISTEN and connection.laddr:
                    result.append((connection.laddr.port, process.pid))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return result


def listeners(only: range | None = None) -> tuple[Listener, ...]:
    """Include IPv4/IPv6 and wildcard listeners; unknown owners remain occupied."""
    found = {}
    for port, pid in _tcp_listeners():
        if only is not None and port not in only:
            continue
        name, created = "unknown process", None
        if pid is not None:
            try:
                process = psutil.Process(pid)
                created, name = process.create_time(), process.name()
            except psutil.Error:
                pass  # fail closed: an uninspectable listener is never ours
        found[port, pid] = Listener(port, pid, name, created)
    return tuple(sorted(found.values(), key=lambda item: (item.port, item.pid or 0)))


def lease(connection: sqlite3.Connection, repo_id: str, job_id: str,
          occupied: Sequence[Listener]) -> int:
    """Idempotently reserve the lowest available complete block in a short tx."""
    with db.tx(connection):
        existing = connection.execute("SELECT base FROM port_leases WHERE repo_id=? AND job_id=?",
                                      (repo_id, job_id)).fetchone()
        if existing:
            return existing[0]
        start, end = map(int, load_machine_settings(connection).ports.range.split("-"))
        leased = [row[0] for row in connection.execute("SELECT base FROM port_leases")]
        blocked = None
        for base in range(start, end - BLOCK_SIZE + 2, BLOCK_SIZE):
            # Also protects against overlap after ports.range was changed.
            if any(base < other + BLOCK_SIZE and other < base + BLOCK_SIZE for other in leased):
                continue
            conflict = next((item for item in occupied if base <= item.port < base + BLOCK_SIZE),
                            None)
            if conflict:
                blocked = blocked or conflict
                continue
            connection.execute(
                "INSERT INTO port_leases (base,repo_id,job_id,leased_at) VALUES (?,?,?,?)",
                (base, repo_id, job_id, pipeline._now()))
            return base
    detail = f"; {blocked.detail}" if blocked else ""
    raise PortsUnavailable(f"No free 10-port block in {start}-{end}{detail}")


def release(connection: sqlite3.Connection, repo_id: str, job_id: str) -> None:
    """Called only for a merged/cancelled/deleted job, never merely on pause."""
    with db.tx(connection):
        connection.execute("DELETE FROM port_leases WHERE repo_id=? AND job_id=?",
                           (repo_id, job_id))


def assigned(base: int | None,
             execution: Mapping[str, Any],
             frozen: Mapping[str, Any] | None = None) -> tuple[int, ...]:
    """Assign execution identities to three slots; excess executions share serially."""
    if base is None:
        return ()
    offset = {"reviewer-ux": 3, "reviewer-ui": 6}.get(execution["uses"], 0)
    if execution["type"] != "task" and execution["uses"] not in {"reviewer-ux", "reviewer-ui"}:
        return ()
    if frozen is not None:
        step = next(s for s in frozen["pipeline"] if s["id"] == execution["step_id"])
        slots = {}
        for entry in step["executions"]:
            role = entry["uses"]
            if step["type"] != "task" and role not in {"reviewer-ux", "reviewer-ui"}:
                continue
            preferred = {"reviewer-ux": 3, "reviewer-ui": 6}.get(role, 0)
            used = set(slots.values())
            slots[entry["id"]] = next((s for s in (preferred, 0, 3, 6) if s not in used),
                                      (len(slots) % 3) * 3)
        offset = slots[execution["exec_id"]]
    return tuple(range(base + offset, base + offset + 3))


def foreign(occupied: Sequence[Listener], base: int,
            owned: Sequence[psutil.Process]) -> Listener | None:
    identities = set()
    for process in owned:
        try:
            if process.is_running():
                identities.add((process.pid, process.create_time()))
        except psutil.Error:
            continue
    return next((item for item in occupied if base <= item.port < base + BLOCK_SIZE and (
        item.create_time is None or (item.pid, item.create_time) not in identities)), None)
