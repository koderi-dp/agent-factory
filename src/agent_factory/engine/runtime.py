"""Machine service coordinator. Serialises job/file mutations with scheduling."""

from collections.abc import Mapping
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from agent_factory import run_cli
from agent_factory.engine import backlog, db, gitops, packet, pipeline, registry, versioning
from agent_factory.engine.adapters.codex import CodexAdapter
from agent_factory.engine.config import load_config, load_machine_settings
from agent_factory.engine.locking import FileLock
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import Supervisor
from agent_factory.paths import machine_dir

LOG = logging.getLogger(__name__)


class Runtime:

    def __init__(self, adapters: Mapping | None = None) -> None:
        self.lock = threading.RLock()
        self.stopping = False
        self.scheduler = Scheduler()
        settings = self.scheduler.machine.call(load_machine_settings)
        self.supervisor = Supervisor(
            self.scheduler, adapters if adapters is not None else {"codex": CodexAdapter(settings)})

    def start(self) -> None:
        with self.lock:
            self.scheduler.refresh()
            # Recover durable notes before pruning their source events.
            for _, writer in self.scheduler.repos.values():
                ids = writer.call(
                    lambda connection: [r[0] for r in connection.execute("SELECT id FROM jobs")])
                for job_id in ids:
                    writer.call(
                        lambda connection: pipeline.materialize_rework_notes(connection, job_id))
            self.supervisor.start()

    def close(self) -> None:
        self.scheduler.close()

    def repo(self, repo_id: str) -> tuple[Path, db.Writer]:
        self.scheduler.refresh()
        if repo_id not in self.scheduler.repos:
            raise KeyError(f"Unknown or missing repo: {repo_id}")
        return self.scheduler.repos[repo_id]

    def tick(self) -> None:
        with self.lock:
            if self.stopping:
                return
            self.scheduler.refresh()
            for repo_id, (root, writer) in list(self.scheduler.repos.items()):
                try:
                    self._drain(repo_id, root, writer)
                except (OSError, ValueError, sqlite3.Error):
                    LOG.exception("Could not prepare queued requests for repo %s", repo_id)
                # Existing jobs use frozen policy even when the current repo config
                # requires another engine. A blocked request cannot stall them.
                jobs = writer.call(lambda connection: [
                    dict(row) for row in connection.execute(
                        "SELECT * FROM jobs WHERE state IN ('queued','approved') ORDER BY seq")
                ])
                for job in jobs:
                    try:
                        if job["state"] == "queued":
                            writer.call(lambda connection: pipeline.advance_job(
                                connection, job["id"], current_head=gitops.branch_head(job)))
                        else:
                            try:
                                writer.call(
                                    lambda connection: gitops.merge(root, connection, job["id"]))
                            except gitops.GitError as error:
                                if not isinstance(error, (gitops.Busy, gitops.Sensitive)):
                                    writer.call(lambda connection: gitops._state(
                                        connection, job["id"], "needs_human", "changes", str(error))
                                                )
                    except (OSError, ValueError, sqlite3.Error):
                        LOG.exception("Could not advance %s/%s", repo_id, job["id"])
            self.supervisor.tick()

    def snapshot(self, repo_id: str, job_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            root, _ = self.repo(repo_id)
            connection = pipeline.open_repo(root)
            try:
                if job_id is not None:
                    job, executions = pipeline.load(connection, job_id)
                    attempts = [
                        dict(row) for row in connection.execute(
                            "SELECT a.* FROM attempts a JOIN executions e ON e.id=a.execution_id "
                            "WHERE e.job_id=? ORDER BY a.id", (job_id, ))
                    ]
                    for attempt in attempts:
                        attempt.pop("owner_token", None)
                        if attempt["usage"]:
                            attempt["usage"] = json.loads(attempt["usage"])
                    return {"job": job, "executions": executions, "attempts": attempts}
                jobs = [
                    pipeline.load(connection, row[0])[0]
                    for row in connection.execute("SELECT id FROM jobs ORDER BY seq")
                ]
                executions = [
                    dict(row) for row in connection.execute(
                        "SELECT * FROM executions WHERE state != 'superseded'")
                ]
                last = connection.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0]
                activity = [
                    dict(row) for row in connection.execute(
                        "SELECT id,last_activity,last_activity_at FROM attempts WHERE state='running'"
                    )
                ]
                return {
                    "jobs": jobs,
                    "executions": executions,
                    "backlog": backlog.items(root / "factory/backlog.md"),
                    "config": load_config(root).model_dump(),
                    "last_event_id": last,
                    "activity": activity
                }
            finally:
                connection.close()

    def overview(self) -> dict[str, Any]:
        with self.lock:
            repos = self.scheduler.machine.call(registry.list_repos)
            result = []
            for repo in repos:
                counts = {"running": 0, "needs-you": 0, "queued": 0}
                status, detail = "missing" if repo.missing else "available", None
                if not repo.missing:
                    try:
                        versioning.check(Path(repo.path))
                    except versioning.VersionMismatch as error:
                        status, detail = "version_mismatch", str(error)
                    connection = pipeline.open_repo(repo.path)
                    try:
                        for state, n in connection.execute(
                                "SELECT state,count(*) FROM jobs GROUP BY state"):
                            if state in {"needs_human", "paused"}:
                                counts["needs-you"] += n
                            elif state in counts:
                                counts[state] += n
                    finally:
                        connection.close()
                result.append({
                    "id": repo.id,
                    "name": repo.name,
                    "path": repo.path,
                    "status": status,
                    "detail": detail,
                    "counts": counts
                })
            return {"repos": result}

    def enqueue(self,
                repo_id: str,
                title: str,
                details: str = "",
                pipeline_name: str | None = None,
                *,
                backlog_key: str | None = None) -> dict[str, Any]:
        with self.lock:
            root, writer = self.repo(repo_id)
            versioning.check(root)
            config = load_config(root)
            frozen = pipeline.freeze(config,
                                     pipeline_name,
                                     preamble=packet.PREAMBLE,
                                     preamble_version=packet.PREAMBLE_VERSION)
            for step in frozen["pipeline"]:
                for entry in step["executions"]:
                    self.supervisor.adapter(entry["provider"])
            if not title.strip() or len(title) > 300 or "\n" in title or "\r" in title:
                raise ValueError("title must be one line of 1 to 300 characters")

            def insert(connection: sqlite3.Connection) -> int:
                with db.tx(connection):
                    seq = connection.execute(
                        "SELECT max(n)+1 FROM (SELECT coalesce(max(seq),0) n FROM jobs "
                        "UNION ALL SELECT coalesce(max(id),0) FROM start_queue)").fetchone()[0]
                    connection.execute(
                        "INSERT INTO start_queue (id,title,details,pipeline,backlog_key,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (seq, title.strip(), details, frozen["pipeline_name"], backlog_key,
                         pipeline._now()))
                    return seq

            try:
                seq = writer.call(insert)
            except sqlite3.IntegrityError:
                raise ValueError("This backlog item has already been started") from None
            self._drain(repo_id, root, writer)
            job_id = writer.call(lambda connection: connection.execute(
                "SELECT job_id FROM start_queue WHERE id=?", (seq, )).fetchone()[0])
            return {"id": job_id, "state": "queued"}

    def _drain(self, repo_id: str, root: Path, writer: db.Writer) -> None:
        rows = writer.call(lambda connection: [
            dict(row) for row in connection.execute(
                "SELECT * FROM start_queue WHERE job_id IS NULL ORDER BY id")
        ])
        for row in rows:
            slug = re.sub(r"[^a-z0-9]+", "-", row["title"].lower()).strip("-")[:60] or "job"
            job_id = f"{row['id']:03}-{slug}"

            def link(connection: sqlite3.Connection) -> None:
                with db.tx(connection):
                    connection.execute("UPDATE start_queue SET job_id=? WHERE id=?",
                                       (job_id, row["id"]))

            exists = writer.call(lambda connection: connection.execute(
                "SELECT 1 FROM jobs WHERE id=?", (job_id, )).fetchone())
            if exists:
                # A crash after create_job committed cannot recreate its worktree
                # or overwrite evidence, even if the job has since merged.
                writer.call(link)
                continue
            config = load_config(root)
            # Refuse new work after a version/config change; existing jobs still run.
            versioning.check(root)
            pipeline.freeze(config,
                            row["pipeline"],
                            preamble=packet.PREAMBLE,
                            preamble_version=packet.PREAMBLE_VERSION)
            path = gitops.worktree_path(root, job_id)
            if not path.exists():
                if gitops.branch_exists(root, f"factory/{job_id}"):
                    gitops.checked("worktree", "add", str(path), f"factory/{job_id}", cwd=root)
                else:
                    gitops.create_worktree(root, job_id)
            folder = root / "factory/jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            request = folder / "request.md"
            if not request.exists():
                run_cli.atomic_write(request, (row["title"] + "\n\n" + (row["details"] or "") +
                                               "\n").encode("utf-8"))
            base = gitops.head(path)

            def create(connection: sqlite3.Connection) -> None:
                pipeline.create_job(connection,
                                    job_id,
                                    row["title"],
                                    config=config,
                                    worktree=str(path),
                                    branch=f"factory/{job_id}",
                                    base_head=base,
                                    preamble=packet.PREAMBLE,
                                    preamble_version=packet.PREAMBLE_VERSION,
                                    pipeline=row["pipeline"])
                # Replayed through the deterministic id if interrupted here.
                link(connection)

            writer.call(create)
        self._repair_backlog(root, writer)

    def _backlog_lock(self, root: Path) -> FileLock:
        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        return FileLock(machine_dir() / "runtime" / f"backlog-{digest}.lock")

    def _repair_backlog(self, root: Path, writer: db.Writer) -> None:
        path = root / "factory/backlog.md"
        rows = writer.call(lambda connection: connection.execute(
            "SELECT backlog_key,job_id FROM start_queue WHERE backlog_key IS NOT NULL AND job_id IS NOT NULL"
        ).fetchall())
        if not rows:
            return
        with self._backlog_lock(root):
            values = backlog.blocks(path)
            links = dict(rows)
            changed = False
            for block in values:
                job_id = links.get(backlog.key(block)) if block["open"] else None
                if job_id:
                    title = backlog.ITEM.match(block["lines"][0])[2]
                    block["lines"][0] = f"- [x] {title} ({job_id})"
                    changed = True
            if changed:
                run_cli.atomic_write(path, backlog.render(values))

    def edit_backlog(self, repo_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            root, writer = self.repo(repo_id)
            versioning.check(root)
            path = root / "factory/backlog.md"
            action = body.get("action")
            # Capture exact item under a file lock. enqueue/replay takes it again.
            with self._backlog_lock(root):
                values = backlog.blocks(path)
                slots = [i for i, block in enumerate(values) if block["open"]]

                def index(key: str) -> int:
                    n = body.get(key, 0 if key == "index" and action == "start" else None)
                    if type(n) is not int or not 0 <= n < len(slots):
                        raise ValueError(f"{key} must name one of the {len(slots)} queued items")
                    return n

                if action == "start":
                    block = values[slots[index("index")]]
                    title = backlog.ITEM.match(block["lines"][0])[2]
                    details = "\n".join(line[2:] if line.startswith("  ") else line
                                        for line in block["lines"][1:])
                    selected = (title, details, backlog.key(block))
                elif action == "add":
                    entries = body.get("items", [{
                        "text": body.get("text"),
                        "details": body.get("details", "")
                    }])
                    if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
                        raise ValueError("items must contain 1 to 50 features")
                    for entry in entries:
                        title = entry.get("text") if isinstance(entry, dict) else None
                        details = entry.get("details", "") if isinstance(entry, dict) else None
                        if not isinstance(title,
                                          str) or not title.strip() or len(title) > 300 or any(
                                              c in title for c in "\n\r"):
                            raise ValueError(
                                "each item needs a one-line title of at most 300 characters")
                        if not isinstance(details, str) or len(details) > 20000:
                            raise ValueError("details must be text of at most 20,000 characters")
                        values.append({
                            "open":
                            True,
                            "lines": [
                                f"- [ ] {title.strip()}",
                                *("  " + line if line else "" for line in details.splitlines())
                            ]
                        })
                elif action == "remove":
                    del values[slots[index("index")]]
                elif action in {"move", "reorder"}:
                    moved = [values[i] for i in slots]
                    moved.insert(index("to"), moved.pop(index("index")))
                    for slot, value in zip(slots, moved):
                        values[slot] = value
                else:
                    raise ValueError("action must be add, remove, move, reorder or start")
                if action != "start":
                    run_cli.atomic_write(path, backlog.render(values))
            if action == "start":
                title, details, key = selected
                return self.enqueue(repo_id, title, details, body.get("pipeline"), backlog_key=key)

            def event(connection: sqlite3.Connection) -> None:
                with db.tx(connection):
                    pipeline._event(connection, None, pipeline._now(), "backlog",
                                    {"action": action})

            writer.call(event)
            return {"backlog": backlog.items(path)}

    def action(self, repo_id: str, job_id: str, action: str, body: dict[str,
                                                                        Any]) -> dict[str, Any]:
        with self.lock:
            root, writer = self.repo(repo_id)
            versioning.check(root)
            if action == "stop":
                self.supervisor.stop(repo_id, job_id)
            elif action == "resume":
                self.scheduler.resume(repo_id, job_id)
            elif action == "rework":
                self.scheduler.check_descendants(repo_id, job_id)
                job = writer.call(lambda connection: pipeline.load(connection, job_id)[0])
                head = gitops.branch_head(job)
                writer.call(lambda connection: pipeline.rework_job(
                    connection, job_id, body.get("note", ""), current_head=head))
            elif action in {"approve", "delete"}:
                # The service lock serialises this with launch and claim. No DB tx
                # is held while git or process-tree operations run.
                if action == "approve":
                    message = writer.call(lambda connection: gitops.merge(
                        root,
                        connection,
                        job_id,
                        human=True,
                        confirm_sensitive=body.get("confirm_sensitive", False)))
                else:
                    message = writer.call(lambda connection: gitops.delete(
                        root, connection, job_id, force=body.get("force", False)))
                self.supervisor.sync_ports()
                return {"message": message}
            else:
                raise KeyError(f"Unknown job action: {action}")
            return self.snapshot(repo_id, job_id)
