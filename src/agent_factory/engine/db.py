"""SQLite connections, short write transactions, and packaged SQL migrations."""

import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


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
    return connection


class Writer:
    """Own a connection on one dedicated thread; never move it between threads.

    Service code submits short synchronous operations. Process/file IO and waits
    belong outside these operations. API reads use their own connections (§6.2).
    """

    def __init__(self, path: str | Path, migrations: str | Path) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="factory-db")

        def open_connection() -> sqlite3.Connection:
            connection = connect(path)
            try:
                migrate(connection, migrations)
            except BaseException:
                connection.close()
                raise
            return connection

        try:
            self._connection = self._executor.submit(open_connection).result()
        except BaseException:
            self._executor.shutdown()
            raise

    def call(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return self._executor.submit(operation, self._connection).result()

    def close(self) -> None:
        self.call(lambda connection: connection.close())
        self._executor.shutdown()


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
