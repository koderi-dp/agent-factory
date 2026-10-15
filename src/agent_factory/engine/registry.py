"""Machine-wide repository registration."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_factory.paths import machine_dir, normalize_repo_path
from agent_factory.engine import db


@dataclass(frozen=True)
class Repo:
    id: str
    path: str
    name: str
    added_at: str
    last_seen: str | None

    @property
    def missing(self) -> bool:
        return self.last_seen is None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_registry() -> sqlite3.Connection:
    connection = db.connect(machine_dir() / "machine.db")
    try:
        db.migrate(connection, Path(__file__).parent / "migrations" / "machine")
    except BaseException:
        connection.close()
        raise
    return connection


def register(connection: sqlite3.Connection, path: str | Path) -> Repo:
    display_name = Path(path).expanduser().resolve().name
    path = normalize_repo_path(path)
    if not (path / "factory" / "config.json").is_file():
        raise ValueError(f"Repo is not initialised: {path}")
    normalized = str(path)
    slug = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-") or "repo"
    repo_id = f"{slug}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:6]}"
    now = _now()
    with db.tx(connection):
        connection.execute(
            "INSERT INTO repos (id, path, name, added_at, last_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET last_seen=excluded.last_seen",
            (repo_id, normalized, display_name, now, now),
        )
        row = connection.execute("SELECT * FROM repos WHERE path=?", (normalized,)).fetchone()
    return Repo(**dict(row))


def list_repos(connection: sqlite3.Connection) -> list[Repo]:
    """Refresh folder availability so restored repos become usable again."""
    rows = connection.execute("SELECT id, path FROM repos").fetchall()
    availability = [(_now() if Path(row["path"]).is_dir() else None, row["id"]) for row in rows]
    with db.tx(connection):
        connection.executemany("UPDATE repos SET last_seen=? WHERE id=?", availability)
        rows = connection.execute("SELECT * FROM repos ORDER BY name, id").fetchall()
    return [Repo(**dict(row)) for row in rows]


def _update(connection: sqlite3.Connection, sql: str, args: tuple) -> None:
    with db.tx(connection):
        if connection.execute(sql, args).rowcount != 1:
            raise KeyError(f"Unknown repo: {args[-1]}")


def remove(connection: sqlite3.Connection, repo_id: str) -> None:
    _update(connection, "DELETE FROM repos WHERE id=?", (repo_id,))


def rename(connection: sqlite3.Connection, repo_id: str, name: str) -> None:
    if not name.strip():
        raise ValueError("Repo name must not be empty")
    _update(connection, "UPDATE repos SET name=? WHERE id=?", (name, repo_id))


def mark_missing(connection: sqlite3.Connection, repo_id: str) -> None:
    _update(connection, "UPDATE repos SET last_seen=NULL WHERE id=?", (repo_id,))
