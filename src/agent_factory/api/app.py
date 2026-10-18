"""Authenticated local API, snapshots and SSE delivery (plan §13.2)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import html
import json
import logging
import os
from pathlib import Path
import secrets
from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import StreamingResponse

from agent_factory import __version__, run_cli
from agent_factory.api import streams
from agent_factory.engine import (backlog, catalog, changes, db, gitops, library, pipeline, roles as
                                  role_files, versioning)
from agent_factory.engine.config import (MachineSettings, load_config, load_machine_settings,
                                         preview_pipeline, save_machine_settings, validate_config)
from agent_factory.engine.runtime import Runtime

BODY_LIMIT = 100_000
JobState = Literal["queued", "running", "paused", "needs_human", "approved", "merged", "cancelled"]
DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard_dist"


class Security:
    """ASGI body limiting counts bytes even for chunked/no-length requests."""

    def __init__(self, app: Any, *, port: int, token: str) -> None:
        self.app, self.port, self.token = app, port, token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])

        def values(name: bytes) -> list[str]:
            return [v.decode("latin-1") for k, v in headers if k.lower() == name]

        hosts = values(b"host")
        status, detail = 0, ""
        if len(hosts) != 1 or hosts[0] not in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}:
            status, detail = 403, "Invalid Host"
        elif scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origins, tokens = values(b"origin"), values(b"x-factory-token")
            if origins and origins != ["http://" + hosts[0]]:
                status, detail = 403, "Cross-site Origin is forbidden"
            elif len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.token):
                status, detail = 403, "Missing or invalid X-Factory-Token"
        messages, size = [], 0
        if not status:
            lengths = values(b"content-length")
            if lengths and (len(lengths) != 1 or not lengths[0].isdecimal()):
                status, detail = 400, "Invalid Content-Length"
            elif lengths and int(lengths[0]) > BODY_LIMIT:
                status, detail = 413, "Body exceeds 100 KB"
        if not status:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > BODY_LIMIT:
                    status, detail = 413, "Body exceeds 100 KB"
                    break
                messages.append(message)
                if not message.get("more_body", False):
                    break
        if status:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
            return

        async def replay() -> dict:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)


class JobBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=300)
    details: str = ""
    pipeline: str | None = None


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = ""
    force: bool = False
    confirm_sensitive: bool = False


def confined(root: Path, path: str) -> Path:
    # resolve handles symlinks and Windows separators, drives and junctions.
    value = Path(path)
    target = (root / value).resolve()
    if value.is_absolute() or target == root.resolve() or not target.is_relative_to(root.resolve()):
        raise HTTPException(403, "File path escapes factory/jobs")
    return target


def create_app(*,
               port: int = 5099,
               token: str | None = None,
               runtime: Runtime | None = None,
               run_engine: bool = True) -> FastAPI:
    token = token or secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or Runtime()
        current = app.state.runtime
        stopped = asyncio.Event()

        async def poll() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.to_thread(current.tick)
                except Exception:
                    logging.getLogger(__name__).exception("Engine tick failed")
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=0.25)
                except TimeoutError:
                    pass

        task = None
        try:
            await asyncio.to_thread(current.start)
            if run_engine:
                task = asyncio.create_task(poll())
            yield
        finally:
            stopped.set()
            if task:
                await task  # Finish any tick before closing DB writers.
            if runtime is None:
                await asyncio.to_thread(current.close)

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(Security, port=port, token=token)
    app.state.shutdown = lambda: None

    @app.exception_handler(KeyError)
    async def unknown(request: Request, error: KeyError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError) -> JSONResponse:
        if isinstance(error, ValidationError):
            return JSONResponse({"detail": json.loads(error.json(include_url=False))},
                                status_code=422)
        code = getattr(error, "exit_code", 1)
        return JSONResponse({
            "detail": str(error),
            "exit_code": code
        },
                            status_code=409 if code in {3, 4} else 400)

    def engine() -> Runtime:
        return app.state.runtime

    def stream_response(iterator) -> StreamingResponse:
        return StreamingResponse(iterator,
                                 media_type="text/event-stream",
                                 headers={
                                     "Cache-Control": "no-cache",
                                     "X-Accel-Buffering": "no"
                                 })

    def cursor(request: Request, initial: int) -> int:
        value = request.headers.get("Last-Event-ID", str(initial))
        if len(value) > 19 or not value.isascii() or not value.isdecimal() or int(
                value) > 2**63 - 1:
            raise HTTPException(400, "Last-Event-ID must be a nonnegative 64-bit integer")
        return int(value)

    @app.get("/api/overview/stream")
    async def overview_events(request: Request):
        return stream_response(streams.overview_stream(request, engine()))

    @app.get("/api/repos/{repo}/events")
    async def events(repo: str,
                     request: Request,
                     after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0):
        await asyncio.to_thread(engine().snapshot, repo)
        return stream_response(streams.repo_stream(request, engine(), repo, cursor(request, after)))

    @app.get("/api/repos/{repo}/attempts/{attempt_id}/stream")
    def attempt_stream(repo: str,
                       attempt_id: int,
                       request: Request,
                       offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0):
        with engine().lock:
            root, writer = engine().repo(repo)

            def attempt():
                return writer.call(lambda connection: connection.execute(
                    "SELECT a.dir,a.state,e.job_id FROM attempts a JOIN executions e "
                    "ON e.id=a.execution_id WHERE a.id=?", (attempt_id, )).fetchone())

            row = attempt()
            if row is None:
                raise HTTPException(404, "Unknown attempt")
            path = confined(root / "factory/jobs", f"{row['job_id']}/{row['dir']}/events.jsonl")

        def finished():
            with engine().lock:
                row = attempt()
                return engine().stopping or row is None or row["state"] not in {
                    "launching", "running"
                }

        return stream_response(streams.log_stream(request, path, cursor(request, offset), finished))

    @app.get("/api/health")
    def health() -> dict:
        return {"version": __version__, "pid": os.getpid()}

    @app.get("/api/overview")
    def overview() -> dict:
        return engine().overview()

    @app.get("/api/jobs")
    def jobs(state: Annotated[list[JobState] | None, Query()] = None,
             repo: str | None = None,
             limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> dict:
        return engine().jobs(state, repo, limit)

    @app.get("/api/activity")
    def activity(limit: Annotated[int, Query(ge=1, le=200)] = 50,
                 repo: str | None = None,
                 cursor: str | None = None,
                 type: Annotated[list[str] | None, Query()] = None) -> dict:
        return engine().activity(limit, repo, cursor, type)

    @app.get("/api/usage")
    def usage(days: Annotated[int, Query(ge=1, le=366)] = 30) -> dict:
        return engine().usage(days, datetime.now(timezone.utc).date())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        dashboard = DASHBOARD_DIR / "index.html"
        if dashboard.is_file():
            return dashboard.read_text(encoding="utf-8").replace(
                "</head>",
                f'<meta name="factory-token" content="{html.escape(token, quote=True)}"></head>',
                1,
            )
        overview = engine().overview()
        sections = []
        for repo in overview["repos"]:
            jobs = []
            if repo["status"] != "missing":
                jobs = engine().snapshot(repo["id"])["jobs"]
            rows = "".join(f"<li>{html.escape(j['id'])}: {html.escape(j['title'])} — "
                           f"{html.escape(j['state'])}</li>" for j in jobs)
            sections.append(
                f"<section><h2>{html.escape(repo['name'])}</h2>"
                f"<p>{html.escape(repo['status'])}</p><ul>{rows or '<li>No jobs</li>'}</ul></section>"
            )
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Agent Factory</title>"
                "<meta http-equiv='refresh' content='5'>"
                f"<meta name='factory-token' content='{html.escape(token, quote=True)}'>"
                "</head><body><h1>Agent Factory</h1>" + "".join(sections) + "</body></html>")

    @app.get("/api/machine/settings")
    def machine_settings() -> dict:
        with engine().lock:
            return engine().scheduler.machine.call(load_machine_settings).model_dump(by_alias=True)

    @app.put("/api/machine/settings")
    def put_machine(body: MachineSettings) -> dict:
        with engine().lock:
            result = engine().scheduler.machine.call(
                lambda connection: save_machine_settings(connection, body))
            for adapter in engine().supervisor.adapters.values():
                if hasattr(adapter, "settings"):
                    adapter.settings = result
            return result.model_dump(by_alias=True)

    @app.get("/api/repos/{repo}/state")
    def state(repo: str) -> dict:
        return engine().snapshot(repo)

    @app.get("/api/repos/{repo}/jobs/{job_id}")
    def job(repo: str, job_id: str) -> dict:
        return engine().snapshot(repo, job_id)

    @app.post("/api/repos/{repo}/jobs", status_code=201)
    def start(repo: str, body: JobBody) -> dict:
        return engine().enqueue(repo, body.title, body.details, body.pipeline)

    @app.post("/api/repos/{repo}/jobs/{job_id}/{action}")
    def action(repo: str, job_id: str, action: str,
               body: ActionBody = Body(default=ActionBody())) -> dict:
        return engine().action(repo, job_id, action, body.model_dump())

    @app.get("/api/repos/{repo}/jobs/{job_id}/files/{path:path}", response_class=PlainTextResponse)
    def file(repo: str, job_id: str, path: str) -> str:
        with engine().lock:
            root, _ = engine().repo(repo)
            engine().snapshot(repo, job_id)
            folder = confined(root / "factory/jobs", job_id)
            target = confined(folder, path)
            if not target.is_file():
                raise HTTPException(404, "File not found")
            # Reports/requests only. Tokens and command env are not public files.
            if target.suffix.lower() != ".md":
                raise HTTPException(403, "Only report/request Markdown files are served here")
            return target.read_text(encoding="utf-8", errors="replace")

    @app.get("/api/repos/{repo}/attempts/{attempt_id}/log")
    def log(repo: str, attempt_id: int, offset: Annotated[int, Query(ge=0)] = 0) -> dict:
        with engine().lock:
            root, writer = engine().repo(repo)
            row = writer.call(lambda connection: connection.execute(
                "SELECT a.dir,e.job_id FROM attempts a JOIN executions e ON e.id=a.execution_id WHERE a.id=?",
                (attempt_id, )).fetchone())
            if row is None:
                raise HTTPException(404, "Unknown attempt")
            target = confined(root / "factory/jobs", f"{row['job_id']}/{row['dir']}/events.jsonl")
            try:
                with target.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read(256 * 1024)
            except FileNotFoundError:
                chunk = b""
            return {"text": chunk.decode("utf-8", errors="replace"), "offset": offset + len(chunk)}

    @app.get("/api/repos/{repo}/config")
    def config(repo: str) -> dict:
        with engine().lock:
            root, _ = engine().repo(repo)
            return load_config(root).model_dump()

    @app.post("/api/repos/{repo}/config/validate")
    def preview_config(repo: str, body: dict = Body()) -> dict:
        # Use the same validation as saving, without changing files or emitting events.
        with engine().lock:
            root, _ = engine().repo(repo)
            parsed = validate_config(body, root=root)
            if not versioning.matches(__version__, parsed.engine):
                raise versioning.VersionMismatch(
                    f"Config requires agent-factory {parsed.engine}; installed {__version__}")
            return {
                "pipelines": {
                    name: pipeline.freeze(parsed, name, preamble="",
                                          preamble_version="")["pipeline"]
                    for name in parsed.pipelines
                },
                "preview": {
                    name: preview_pipeline(parsed, name)
                    for name in parsed.pipelines
                }
            }

    @app.put("/api/repos/{repo}/config")
    def put_config(repo: str, body: dict = Body()) -> dict:
        with engine().lock:
            root, writer = engine().repo(repo)
            versioning.check(root)
            parsed = validate_config(body, root=root)
            # Validate the submitted gate too, before replacing a usable config.
            if not versioning.matches(__version__, parsed.engine):
                raise versioning.VersionMismatch(
                    f"Config requires agent-factory {parsed.engine}; installed {__version__}")
            run_cli.atomic_json(root / "factory/config.json", parsed.model_dump())

            def event(connection):
                with db.tx(connection):
                    pipeline._event(connection, None, pipeline._now(), "config", {})

            writer.call(event)
            return parsed.model_dump()

    @app.get("/api/library/pipelines")
    def library_pipelines() -> dict:
        return {"pipelines": library.list_pipelines()}

    @app.put("/api/library/pipelines/{name}")
    def save_library_pipeline(name: str, body: dict = Body()) -> dict:
        steps = body.get("steps")
        if not isinstance(steps, list):
            raise ValueError("steps must be a list of pipeline steps")
        return library.save(name, steps)

    @app.delete("/api/library/pipelines/{name}")
    def delete_library_pipeline(name: str) -> dict:
        library.delete(name)
        return {"deleted": name}

    @app.get("/api/providers/models")
    async def provider_models(refresh: bool = False) -> dict:

        def read() -> dict:
            with engine().lock:
                settings = engine().scheduler.machine.call(load_machine_settings)
            # The tools run outside the engine lock: OpenCode can take seconds to answer.
            return catalog.discover(settings.cli.model_dump(), refresh=refresh)

        return {"providers": await asyncio.to_thread(read)}

    @app.get("/api/repos/{repo}/roles/available")
    def available_roles(repo: str) -> dict:
        with engine().lock:
            root, _ = engine().repo(repo)
            return {"roles": role_files.available_roles(root)}

    @app.get("/api/repos/{repo}/roles")
    def roles(repo: str) -> dict:
        with engine().lock:
            root, _ = engine().repo(repo)
            config = load_config(root)
            result = {}
            for name, steps in config.pipelines.items():
                for step in steps:
                    for entry in step.executions:
                        role = config.role_for(name, entry.id)
                        result[entry.uses, entry.provider] = {
                            "name": role.name,
                            "provider": role.provider,
                            "type": role.type,
                            "source": role.source,
                            "sha256": role.sha256,
                            **role_files.provenance(role, root=root)
                        }
            return {"roles": list(result.values())}

    @app.get("/api/repos/{repo}/backlog")
    def list_backlog(repo: str) -> dict:
        with engine().lock:
            root, _ = engine().repo(repo)
            path = root / "factory/backlog.md"
            return {"backlog": backlog.items(path), "started": backlog.started(path)}

    @app.put("/api/repos/{repo}/backlog/meta")
    def edit_backlog_meta(repo: str, body: dict = Body()) -> dict:
        return engine().edit_backlog_meta(repo, body)

    @app.post("/api/repos/{repo}/backlog")
    def edit_backlog(repo: str, body: dict = Body()) -> dict:
        return engine().edit_backlog(repo, body)

    @app.post("/api/service/stop")
    def shutdown(body: dict = Body(default={})) -> dict:
        with engine().lock:
            engine().stopping = True
            if body.get("stop_running", False):
                for repo_id, (_, writer) in list(engine().scheduler.repos.items()):
                    ids = writer.call(lambda connection: [
                        row[0] for row in connection.execute(
                            "SELECT id FROM jobs WHERE state IN ('running','paused')")
                    ])
                    for job_id in ids:
                        engine().supervisor.stop(repo_id, job_id)
                # Keep polling until the graceful Stop interval and tree cleanup finish.
                import time
                deadline = time.monotonic() + 20
                while engine().scheduler.recount():
                    if time.monotonic() > deadline:
                        raise ValueError("Timed out waiting for running attempts to stop")
                    engine().supervisor.poll()
                    time.sleep(0.05)
            app.state.shutdown()
        return {"stopping": True}

    def load_job(repo: str, job_id: str) -> tuple[Path, dict]:
        with engine().lock:
            root, writer = engine().repo(repo)
            try:
                job = writer.call(lambda connection: pipeline.load(connection, job_id))[0]
            except pipeline.PipelineError as error:
                raise HTTPException(404, "Unknown job") from error
        return root, job

    @app.get("/api/repos/{repo}/jobs/{job_id}/changes")
    def changes_list(repo: str, job_id: str) -> dict:
        root, job = load_job(repo, job_id)
        return changes.list_changes(root, job)

    @app.get("/api/repos/{repo}/jobs/{job_id}/changes/diff")
    def change_diff(repo: str, job_id: str, path: Annotated[str, Query(min_length=1)]) -> dict:
        root, job = load_job(repo, job_id)
        try:
            return changes.file_diff(root, job, path)
        except changes.UnknownFile as error:
            raise HTTPException(404, "The job did not change this file") from error

    @app.get("/{asset_path:path}")
    def dashboard_asset(asset_path: str):
        target = (DASHBOARD_DIR / asset_path).resolve()
        if not target.is_relative_to(DASHBOARD_DIR.resolve()) or not target.is_file():
            raise HTTPException(404, "Dashboard asset not found")
        return FileResponse(target)

    return app
