"""SQLite connections, short write transactions, and packaged SQL migrations."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open an autocommit connection; writes must use tx()."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        connection.close()
        raise
    # TODO (plan §6.2, §6.4): the service owns one writer thread per open DB.
    return connection


@contextmanager
def tx(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit a short, exclusive writer transaction or roll it back on error."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def migrate(connection: sqlite3.Connection, directory: str | Path) -> None:
    """Apply a contiguous numbered migration set atomically, once per version."""
    files = sorted(Path(directory).glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [int(path.name.split("_", 1)[0]) for path in files]
    if versions != list(range(1, len(files) + 1)):
        raise ValueError("Migrations must be numbered consecutively starting at 0001")
    with tx(connection):
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > len(files):
            raise ValueError(f"Database version {current} is newer than available migrations")
        for number, path in zip(versions, files):
            if number <= current:
                continue
            # executescript() implicitly commits; execute complete statements instead.
            statement = ""
            for char in path.read_text(encoding="utf-8"):
                statement += char
                if char == ";" and sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
            if statement.strip():
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={number}")
