"""Bounded SSE delivery. No database transactions or runtime locks span a yield."""

import asyncio
from collections.abc import AsyncIterator, Callable
import json
import time
from pathlib import Path

from starlette.requests import Request

from agent_factory.engine import pipeline
from agent_factory.engine.runtime import Runtime

REPLAY_LIMIT = 5000
LOG_BATCH = 64 * 1024


def frame(kind: str, data: dict, identifier: int | None = None) -> str:
    prefix = f"id: {identifier}\n" if identifier is not None else ""
    return prefix + f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


def replay(runtime: Runtime, repo: str, after: int) -> tuple[bool, dict, list[dict]]:
    with runtime.lock:
        root, _ = runtime.repo(repo)
        connection = pipeline.open_repo(root)
        try:
            last = connection.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0]
            present = after == 0 or connection.execute("SELECT 1 FROM events WHERE id=?",
                                                       (after, )).fetchone() is not None
            rows = [
                dict(row)
                for row in connection.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?",
                                              (after, REPLAY_LIMIT + 1))
            ]
        finally:
            connection.close()
        reset = after > last or not present or len(rows) > REPLAY_LIMIT
        state = runtime.snapshot(repo)
        if reset:
            return True, state, []
        # Durable events are deliberately small partial updates. Attach an authoritative
        # snapshot and affected job detail, so creations, deletions and rewinds reduce
        # correctly without a second request racing the stream.
        details = {}
        for row in rows:
            job_id = row["job_id"]
            if job_id and job_id not in details:
                try:
                    details[job_id] = runtime.snapshot(repo, job_id)
                except (KeyError, pipeline.PipelineError):
                    details[job_id] = None
            row["payload"] = json.loads(row["payload"])
            row["state"] = state
            row["detail"] = details.get(job_id)
        return False, state, rows


async def repo_stream(request: Request, runtime: Runtime, repo: str,
                      after: int) -> AsyncIterator[str]:
    heartbeat = time.monotonic()
    sent_activity: dict[int, tuple[str, float]] = {}
    while not runtime.stopping and not await request.is_disconnected():
        reset, state, rows = await asyncio.to_thread(replay, runtime, repo, after)
        if reset:
            after = state["last_event_id"]
            yield frame("reset", {"last_event_id": after}, after)
        else:
            for row in rows:
                after = row["id"]
                yield frame(row["type"], row, after)
        now = time.monotonic()
        running = {item["attempt_id"] for item in state["activity"]}
        sent_activity = {key: value for key, value in sent_activity.items() if key in running}
        for item in state["activity"]:
            previous, sent = sent_activity.get(item["attempt_id"], ("", -float("inf")))
            if item["at"] and item["at"] != previous and now - sent >= 1:
                sent_activity[item["attempt_id"]] = item["at"], now
                yield frame("activity", item)
        if now - heartbeat >= 15:
            heartbeat = now
            yield ": keepalive\n\n"
        await asyncio.sleep(0.25)


async def overview_stream(request: Request, runtime: Runtime) -> AsyncIterator[str]:
    previous = None
    heartbeat = time.monotonic()
    while not runtime.stopping and not await request.is_disconnected():
        current = await asyncio.to_thread(runtime.overview)
        if current != previous:
            previous = current
            yield frame("overview", current)
        if time.monotonic() - heartbeat >= 15:
            heartbeat = time.monotonic()
            yield ": keepalive\n\n"
        await asyncio.sleep(0.5)


def log_batch(path: Path, offset: int, *, final: bool = False) -> tuple[bytes, int]:
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(LOG_BATCH)
    except FileNotFoundError:
        return b"", offset
    # Split oversized lines at the hard cap, but never split a UTF-8 character.
    end = len(chunk) if final or len(chunk) == LOG_BATCH else chunk.rfind(b"\n") + 1
    data = chunk[:end]
    if data and (not final or len(chunk) == LOG_BATCH):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data":
                data = data[:error.start]
    return data, offset + len(data)


async def log_stream(request: Request, path: Path, offset: int,
                     finished: Callable[[], bool]) -> AsyncIterator[str]:
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        final = await asyncio.to_thread(finished)
        chunk, offset = await asyncio.to_thread(log_batch, path, offset, final=final)
        if chunk:
            yield frame("log", {
                "text": chunk.decode("utf-8", errors="replace"),
                "offset": offset
            }, offset)
            if len(chunk) >= LOG_BATCH - 3:
                continue
        elif final:
            yield frame("end", {"offset": offset}, offset)
            return
        if time.monotonic() - heartbeat >= 15:
            heartbeat = time.monotonic()
            yield ": keepalive\n\n"
        await asyncio.sleep(0.5)
