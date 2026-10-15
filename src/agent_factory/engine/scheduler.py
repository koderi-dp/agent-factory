"""Transactional claims, machine-wide capacity and round-robin repos (§6.4)."""

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_factory.engine import db, pipeline, ports, registry
from agent_factory.engine.config import load_machine_settings
from agent_factory.engine.crash import crash_at
from agent_factory.paths import machine_dir

MIGRATIONS = Path(__file__).parent / "migrations"
LIVE = "('launching','running')"


@dataclass(frozen=True)
class Claim:
    repo_id: str
    root: Path
    job: dict[str, Any]
    execution: dict[str, Any]
    attempt: dict[str, Any]
    previous: dict[str, Any] | None = None

    @property
    def directory(self) -> Path:
        return self.root / "factory" / "jobs" / self.job["id"] / self.attempt["dir"]

    @property
    def ports(self) -> tuple[int, ...]:
        return ports.assigned(self.job["port_base"], self.execution)


class Scheduler:
    """One service owns this scheduler and one writer per registered database.

    The machine DB write lock serialises capacity checks + repo claims even for
    competing scheduler instances. It is released before prepare/spawn/tailing.
    Recovery MUST finish before claims are enabled by Supervisor.start().
    """

    def __init__(self) -> None:
        self.machine = db.Writer(machine_dir() / "machine.db", MIGRATIONS / "machine")
        self.repos: dict[str, tuple[Path, db.Writer]] = {}
        self.live_global = 0
        self.ready = False
        self._cursor = 0
        self._lock = threading.RLock()
        self.refresh()

    def refresh(self) -> None:
        with self._lock:
            repos = self.machine.call(registry.list_repos)
            available = {repo.id: repo for repo in repos if not repo.missing}
            for identifier in set(self.repos) - available.keys():
                self.repos.pop(identifier)[1].close()
            for repo in available.values():
                if repo.id not in self.repos:
                    root = Path(repo.path)
                    writer = db.Writer(root / "factory/state/factory.db", MIGRATIONS / "repo")
                    self.repos[repo.id] = (root, writer)
                    # New/returning repos also need recovery before dispatch.
                    self.ready = False

    def close(self) -> None:
        # Closing a service must leave detached wrappers running (§3.3).
        for _, writer in self.repos.values():
            writer.close()
        self.machine.close()

    def recount(self) -> int:
        self.live_global = sum(writer.call(lambda connection: connection.execute(
            f"SELECT count(*) FROM attempts WHERE state IN {LIVE}").fetchone()[0])
            for _, writer in self.repos.values())
        return self.live_global

    def context(self, repo_id: str, attempt_id: int) -> Claim:
        root, writer = self.repos[repo_id]

        def read(connection: sqlite3.Connection) -> Claim:
            attempt = dict(connection.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone())
            execution = dict(connection.execute("SELECT * FROM executions WHERE id=?",
                                                (attempt["execution_id"],)).fetchone())
            job, _ = pipeline.load(connection, execution["job_id"])
            previous = connection.execute("SELECT * FROM attempts WHERE execution_id=? AND n<? ORDER BY n DESC LIMIT 1",
                                          (execution["id"], attempt["n"])).fetchone()
            return Claim(repo_id, root, job, execution, attempt, dict(previous) if previous else None)

        return writer.call(read)

    def claim_next(self) -> Claim | None:
        with self._lock:
            if not self.ready:
                raise RuntimeError("Recover every registered repo before scheduling")
            identifiers = list(self.repos)
            if not identifiers:
                return None

            def claim_under_capacity(machine: sqlite3.Connection) -> Claim | None:
                # No process launch, git, file IO or await while holding this lock.
                with db.tx(machine):
                    limit = load_machine_settings(machine).concurrency.global_
                    if self.recount() >= limit:
                        return None
                    for offset in range(len(identifiers)):
                        index = (self._cursor + offset) % len(identifiers)
                        identifier = identifiers[index]
                        root, writer = self.repos[identifier]
                        result = writer.call(lambda connection: self._claim(connection, identifier, root))
                        if result is not None:
                            self._cursor = (index + 1) % len(identifiers)
                            self.live_global += 1
                            return result
                return None

            result = self.machine.call(claim_under_capacity)
            if result is not None:
                crash_at("after_claim")
            return result

    @staticmethod
    def _claim(connection: sqlite3.Connection, repo_id: str, root: Path) -> Claim | None:
        with db.tx(connection):
            jobs = connection.execute("SELECT id FROM jobs WHERE state='running' ORDER BY seq").fetchall()
            for row in jobs:
                job, executions = pipeline.load(connection, row["id"])
                live = connection.execute(
                    f"SELECT count(*) FROM attempts a JOIN executions e ON e.id=a.execution_id "
                    f"WHERE e.job_id=? AND a.state IN {LIVE}", (job["id"],)).fetchone()[0]
                if live >= job["frozen"]["concurrency"]["perJob"]:
                    continue
                for execution in pipeline.step_plan(job, executions):
                    # Conditional claim + partial unique index protect stale readers.
                    if connection.execute(f"SELECT 1 FROM attempts WHERE execution_id=? AND state IN {LIVE}",
                                          (execution["id"],)).fetchone():
                        continue
                    previous = connection.execute("SELECT * FROM attempts WHERE execution_id=? ORDER BY n DESC LIMIT 1",
                                                  (execution["id"],)).fetchone()
                    previous = dict(previous) if previous else None
                    kind, session_id = "fresh", None
                    if previous and previous["state"] == "invalid":
                        kind = "contract_retry"
                    elif previous and previous["session_id"]:
                        kind, session_id = "resume", previous["session_id"]
                    if execution["provider"] == "claude" and kind != "resume":
                        session_id = str(uuid.uuid4())
                    n = previous["n"] + 1 if previous else 1
                    directory = f"executions/{execution['step_id']}.{execution['exec_id']}/r{execution['round']}/a{n}"
                    now, token = pipeline._now(), str(uuid.uuid4())
                    cursor = connection.execute(
                        "INSERT INTO attempts (execution_id,n,kind,state,owner_token,session_id,dir,t_queued) "
                        "VALUES (?,?,?,'launching',?,?,?,?)",
                        (execution["id"], n, kind, token, session_id, directory, now))
                    connection.execute("UPDATE executions SET state='claimed' WHERE id=? AND state='pending'",
                                       (execution["id"],))
                    pipeline._event(connection, job["id"], now, "attempt.state",
                                    {"id": cursor.lastrowid, "state": "launching"})
                    pipeline._event(connection, job["id"], now, "execution.state",
                                    {"id": execution["id"], "state": "claimed"})
                    attempt = dict(connection.execute("SELECT * FROM attempts WHERE id=?", (cursor.lastrowid,)).fetchone())
                    return Claim(repo_id, root, job, execution, attempt, previous)
        return None

    def resume(self, repo_id: str, job_id: str) -> None:
        """Explicit Resume permits retries; lost attempts are never auto-retried."""
        def apply(connection: sqlite3.Connection) -> None:
            with db.tx(connection):
                job, _ = pipeline.load(connection, job_id)
                if job["state"] != "paused":
                    raise ValueError("only paused jobs can be resumed")
                if connection.execute(f"SELECT 1 FROM attempts a JOIN executions e ON e.id=a.execution_id "
                                      f"WHERE e.job_id=? AND a.state IN {LIVE} "
                                      "AND (a.error_class='timeout' OR a.error_detail='Stopped by user')",
                                      (job_id,)).fetchone():
                    raise ValueError("wait for stopping attempts to exit before resuming")
                now = pipeline._now()
                connection.execute("UPDATE jobs SET state='running',pause_reason=NULL,note=NULL,updated_at=? WHERE id=?",
                                   (now, job_id))
                connection.execute("UPDATE executions SET state='pending' WHERE job_id=? AND step_id=? AND round=? "
                                   "AND state IN ('failed','stopped')", (job_id, job["current_step"], job["round"]))
                pipeline._event(connection, job_id, now, "job.state", {"state": "running"})
        self.repos[repo_id][1].call(apply)
