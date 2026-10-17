import json
import sqlite3

from agent_factory.engine import usage


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE executions (id INTEGER PRIMARY KEY, provider TEXT);"
        "CREATE TABLE attempts (id INTEGER PRIMARY KEY, execution_id INTEGER, t_queued TEXT,"
        " t_launch TEXT, usage TEXT);"
        "INSERT INTO executions VALUES (1, 'opencode'), (2, 'codex');")
    return connection


def _attempt(connection, execution, launch, value, queued=None):
    connection.execute(
        "INSERT INTO attempts (execution_id,t_queued,t_launch,usage) VALUES (?,?,?,?)",
        (execution, queued, launch, value))


def test_two_attempts_on_a_day_are_summed():
    connection = _connection()
    for n in (10, 5):
        _attempt(connection, 1, "2026-10-16T09:00:00Z",
                 json.dumps({
                     "input": n,
                     "cached_input": 1,
                     "output": 2,
                     "cost_usd": 0.5
                 }))
    [bucket] = usage.daily(connection, since="2026-10-01")
    assert bucket["attempts"] == 2
    assert bucket["input"] == {"value": 15, "unknown": 0}
    assert bucket["cost_usd"] == {"value": 1.0, "unknown": 0}


def test_missing_usage_is_unknown_not_zero():
    connection = _connection()
    _attempt(connection, 2, "2026-10-16T09:00:00Z", None)
    _attempt(connection, 2, "2026-10-16T10:00:00Z", "not json")
    _attempt(connection,
             2,
             None,
             json.dumps({
                 "input": 3,
                 "output": None,
                 "cost_usd": None
             }),
             queued="2026-10-16T11:00:00Z")
    [bucket] = usage.daily(connection, since="2026-10-01")
    assert bucket["input"] == {"value": 3, "unknown": 2}
    assert bucket["output"] == {"value": None, "unknown": 3}
    assert bucket["cost_usd"] == {"value": None, "unknown": 3}


def test_window_and_untimed_attempts():
    connection = _connection()
    _attempt(connection, 1, "2026-09-01T00:00:00Z", "{}")
    _attempt(connection, 1, None, "{}")
    _attempt(connection, 1, "2026-10-16T00:00:00Z", "{}")
    assert [b["day"] for b in usage.daily(connection, since="2026-10-01")] == ["2026-10-16"]
