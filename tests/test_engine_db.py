"""SQLite migration and transaction contracts for the new engine."""

import sqlite3
from pathlib import Path

import pytest

from agent_factory.engine import db


@pytest.fixture
def connection(tmp_path):
    connection = db.connect(tmp_path / "state" / "test.db")
    yield connection
    connection.close()


def test_connection_pragmas(connection):
    for name, expected in (("journal_mode", "wal"), ("synchronous", 1), ("foreign_keys", 1),
                           ("busy_timeout", 5000)):
        assert connection.execute(f"PRAGMA {name}").fetchone()[0] == expected


def test_machine_migrations_apply_once(connection):
    directory = Path(db.__file__).parent / "migrations" / "machine"
    db.migrate(connection, directory)
    with db.tx(connection):
        connection.execute("INSERT INTO settings VALUES ('test', 'kept')")
    db.migrate(connection, directory)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute("SELECT value FROM settings").fetchone()[0] == "kept"
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables == {"repos", "port_leases", "settings"}


def test_tx_commits_and_rolls_back(connection):
    connection.execute("CREATE TABLE test (value TEXT)")
    statements = []
    connection.set_trace_callback(statements.append)
    with db.tx(connection):
        connection.execute("INSERT INTO test VALUES ('kept')")
    with pytest.raises(RuntimeError):
        with db.tx(connection):
            connection.execute("INSERT INTO test VALUES ('discarded')")
            raise RuntimeError("abort")
    assert [row[0] for row in connection.execute("SELECT value FROM test")] == ["kept"]
    assert statements.count("BEGIN IMMEDIATE") == 2
    assert not connection.in_transaction


def test_failed_migration_is_atomic_and_retryable(connection, tmp_path):
    migration = tmp_path / "0001_test.sql"
    migration.write_text("CREATE TABLE test (value TEXT); INSERT INTO absent VALUES (1);",
                         encoding="utf-8")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(connection, tmp_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='test'").fetchone() is None
    migration.write_text("CREATE TABLE test (value TEXT); INSERT INTO test VALUES ('a;b');",
                         encoding="utf-8")
    db.migrate(connection, tmp_path)
    (tmp_path / "0002_next.sql").write_text("ALTER TABLE test ADD COLUMN extra TEXT;",
                                            encoding="utf-8")
    db.migrate(connection, tmp_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert connection.execute("SELECT value FROM test").fetchone()[0] == "a;b"


def test_migration_rejects_gaps_and_newer_database(connection, tmp_path):
    (tmp_path / "0002_gap.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="consecutively"):
        db.migrate(connection, tmp_path)
    connection.execute("PRAGMA user_version=5")
    with pytest.raises(ValueError, match="newer"):
        db.migrate(connection, Path(db.__file__).parent / "migrations" / "machine")


def test_repo_migration_idempotence_foreign_keys_and_live_attempt_index(connection):
    directory = Path(db.__file__).parent / "migrations" / "repo"
    db.migrate(connection, directory)
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables == {"jobs", "executions", "attempts", "events", "start_queue", "sqlite_sequence"}
    with db.tx(connection):
        connection.execute(
            "INSERT INTO jobs (id, seq, title, pipeline_name, frozen, state, branch, worktree, created_at, updated_at) "
            "VALUES ('001-job', 1, 'Feature', 'full', '{}', 'queued', 'factory/001-job', 'worktree', 'now', 'now')"
        )
        execution_id = connection.execute(
            "INSERT INTO executions (job_id, step_id, exec_id, round, type, uses, provider, model, effort, state, created_at) "
            "VALUES ('001-job', 'build', 'build', 1, 'task', 'builder', 'codex', 'model', 'medium', 'pending', 'now')"
        ).lastrowid
        connection.execute(
            "INSERT INTO attempts (execution_id, n, kind, state, owner_token, dir) VALUES (?, 1, 'fresh', 'running', 'owner1', 'a1')",
            (execution_id, ))
        connection.execute(
            "INSERT INTO start_queue (title, backlog_key, created_at, job_id) VALUES ('Feature', 'key', 'now', '001-job')"
        )
    db.migrate(connection, directory)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute("SELECT title FROM jobs").fetchone()[0] == "Feature"
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx(connection):
            connection.execute(
                "INSERT INTO attempts (execution_id, n, kind, state, owner_token, dir) VALUES (?, 2, 'resume', 'launching', 'owner2', 'a2')",
                (execution_id, ))
    with db.tx(connection):
        connection.execute("UPDATE attempts SET state='valid'")
        connection.execute(
            "INSERT INTO attempts (execution_id, n, kind, state, owner_token, dir) VALUES (?, 2, 'resume', 'launching', 'owner2', 'a2')",
            (execution_id, ))
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx(connection):
            connection.execute("DELETE FROM jobs")  # start_queue intentionally keeps its FK.
    with db.tx(connection):
        connection.execute("DELETE FROM start_queue")
        connection.execute("DELETE FROM jobs")
    assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
