"""Session 3.2: real SQLite replay plus bounded, ephemeral streaming delivery."""

import asyncio
import json

import pytest

from agent_factory.api import streams
from agent_factory.engine import db, pipeline
from agent_factory.engine.activity import Activity
from test_engine_api import api, repo  # noqa: F401 -- shared isolated API fixtures


def add_events(runtime, repo_id, count, kind="note"):
    _, writer = runtime.repo(repo_id)

    def write(connection):
        with db.tx(connection):
            for n in range(count):
                pipeline._event(connection, None, pipeline._now(), kind, {"n": n})

    writer.call(write)


class Connected:

    async def is_disconnected(self):
        return False


def test_replay_order_and_authoritative_snapshots(api):
    _, runtime, repo_id, _ = api
    add_events(runtime, repo_id, 3)
    reset, state, rows = streams.replay(runtime, repo_id, 1)
    assert not reset
    assert [row["id"] for row in rows] == [2, 3]
    assert rows[0]["payload"] == {"n": 1}
    assert rows[0]["state"] == state
    assert state["last_event_id"] == 3
    assert streams.replay(runtime, repo_id, 3)[2] == []


@pytest.mark.parametrize("after,prune,count", [(999, False, 3), (1, True, 3), (0, False, 5001)])
def test_reset_future_pruned_and_excessive_gap(api, after, prune, count):
    _, runtime, repo_id, _ = api
    add_events(runtime, repo_id, count)
    if prune:
        runtime.repo(repo_id)[1].call(
            lambda connection: connection.execute("DELETE FROM events WHERE id=1"))
    reset, state, rows = streams.replay(runtime, repo_id, after)
    assert reset and rows == []

    async def first():
        stream = streams.repo_stream(Connected(), runtime, repo_id, after)
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    frame = asyncio.run(first())
    assert "event: reset" in frame
    assert f"id: {state['last_event_id']}" in frame


def test_replay_limit_is_inclusive(api):
    _, runtime, repo_id, _ = api
    add_events(runtime, repo_id, 5000)
    assert not streams.replay(runtime, repo_id, 0)[0]


def test_activity_throttle_and_persistence_interval(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("agent_factory.engine.activity.time.monotonic", lambda: clock[0])
    activity = Activity()
    assert activity.update("repo", 1, "first", "2026-01-01T00:00:00Z")
    clock[0] = 1
    assert not activity.update("repo", 1, "latest", "2026-01-01T00:00:01Z")
    assert activity.get("repo", 1)["text"] == "latest"
    clock[0] = 10
    assert activity.update("repo", 1, "persist", "2026-01-01T00:00:10Z")
    assert activity.current("repo", set()) == []
    assert activity.get("repo", 1) is None


def test_log_batches_offsets_utf8_and_partial_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"a\n" + b"b" * (streams.LOG_BATCH - 3) + "č".encode() + b"\npartial")
    first, offset = streams.log_batch(path, 0)
    assert len(first) <= streams.LOG_BATCH
    first.decode("utf-8")
    second, offset2 = streams.log_batch(path, offset)
    assert first + second == path.read_bytes().split(b"partial")[0]
    assert streams.log_batch(path, offset2) == (b"", offset2)
    assert streams.log_batch(path, offset2, final=True) == (b"partial", path.stat().st_size)
    assert streams.log_batch(tmp_path / "missing", 0) == (b"", 0)


def test_log_stream_batches_and_finishes_without_db_writes(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"x" * (streams.LOG_BATCH * 2 + 3))

    async def consume():
        return [frame async for frame in streams.log_stream(Connected(), path, 0, lambda: True)]

    frames = asyncio.run(consume())
    batches = [json.loads(frame.split("data: ")[1]) for frame in frames[:-1]]
    assert [len(batch["text"]) for batch in batches] == [streams.LOG_BATCH, streams.LOG_BATCH, 3]
    assert batches[-1]["offset"] == path.stat().st_size
    assert "event: end" in frames[-1]


def test_http_stream_last_event_id_and_validation(api):
    client, runtime, repo_id, _ = api
    add_events(runtime, repo_id, 3)

    # TestClient buffers infinite responses. Drive ASGI until the first actual body
    # frame, then disconnect, exercising middleware, header parsing and cancellation.
    async def first(path, headers=()):
        captured = []
        done = asyncio.Event()
        initial = True

        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {"type": "http.request", "body": b""}
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            captured.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                done.set()

        await asyncio.wait_for(
            client.app(
                {
                    "type": "http",
                    "asgi": {
                        "version": "3.0"
                    },
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"after=0",
                    "headers": [(b"host", b"127.0.0.1:5099"), *headers],
                    "server": ("127.0.0.1", 5099),
                    "client": ("127.0.0.1", 1234),
                }, receive, send), 5)
        return captured

    messages = client.portal.call(first, f"/api/repos/{repo_id}/events", [(b"last-event-id", b"2")])
    assert messages[0]["status"] == 200
    assert b"text/event-stream" in dict(messages[0]["headers"])[b"content-type"]
    assert b"id: 3\n" in messages[1]["body"]
    assert client.get(f"/api/repos/{repo_id}/events", headers={
        "Last-Event-ID": "bad"
    }).status_code == 400
    assert client.get("/api/repos/missing/events").status_code == 404
    assert client.get(f"/api/repos/{repo_id}/attempts/999/stream").status_code == 404
    messages = client.portal.call(first, "/api/overview/stream")
    assert b"event: overview" in messages[1]["body"]


def test_activity_delivery_is_ephemeral_and_throttled(api, monkeypatch):
    _, runtime, repo_id, _ = api
    now = [100.0]
    state = {
        "last_event_id": 0,
        "activity": [{
            "attempt_id": 1,
            "text": "reading first",
            "at": "2026-01-01T00:00:00Z"
        }]
    }
    monkeypatch.setattr(streams, "replay", lambda *args: (False, state, []))
    monkeypatch.setattr(streams.time, "monotonic", lambda: now[0])

    async def advance(seconds):
        now[0] += seconds
        state["activity"][0] = {
            "attempt_id": 1,
            "text": "latest",
            "at": f"2026-01-01T00:00:{int(now[0])}Z"
        }

    monkeypatch.setattr(streams.asyncio, "sleep", advance)

    async def consume():
        generator = streams.repo_stream(Connected(), runtime, repo_id, 0)
        try:
            first = await anext(generator)
            first_at = now[0]
            second = await anext(generator)
            return first, second, now[0] - first_at
        finally:
            await generator.aclose()

    first, second, elapsed = asyncio.run(consume())
    assert "event: activity" in first and "event: activity" in second
    assert "id:" not in first
    assert elapsed >= 1
    assert runtime.repo(repo_id)[1].call(
        lambda connection: connection.execute("SELECT count(*) FROM events").fetchone()[0]) == 0


def test_log_batch_flushes_on_interval_and_keeps_byte_cursor(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_bytes("č\n".encode())
    waits = []

    async def tick(seconds):
        waits.append(seconds)
        path.write_bytes("č\nsecond\n".encode())

    monkeypatch.setattr(streams.asyncio, "sleep", tick)

    async def consume():
        generator = streams.log_stream(Connected(), path, 0, lambda: False)
        try:
            first = await anext(generator)
            second = await anext(generator)
            return first, second
        finally:
            await generator.aclose()

    first, second = asyncio.run(consume())
    assert '"offset": 3' in first
    assert '"offset": 10' in second
    assert waits == [0.5]
