"""Single-instance localhost service and the CLI's authenticated HTTP client."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import psutil

from agent_factory import __version__, run_cli
from agent_factory.engine.locking import FileLock
from agent_factory.paths import machine_dir


class ServiceError(ValueError):

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def info() -> dict[str, Any] | None:
    try:
        data = json.loads((machine_dir() / "service.json").read_text(encoding="utf-8"))
        process = psutil.Process(data["pid"])
        if process.create_time() == data["create_time"] and process.is_running():
            return data
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    return None


def url(data: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{data['port']}"


def request(data: dict[str, Any],
            path: str,
            body: Any = None,
            *,
            method: str | None = None,
            timeout: float = 10) -> Any:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url(data) + path,
                  data=payload,
                  method=method,
                  headers={
                      "X-Factory-Token": data["token"],
                      "Content-Type": "application/json"
                  })
    try:
        # Never send the local token through a machine's HTTP proxy.
        with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            result = json.load(error)
            detail = result.get("detail", result)
            code = result.get("exit_code", 1)
        except (ValueError, AttributeError):
            detail, code = str(error), 1
        raise ServiceError(str(detail), code) from None


def healthy(data: dict[str, Any] | None) -> bool:
    if data is None:
        return False
    try:
        result = request(data, "/api/health", timeout=0.5)
        return result["pid"] == data["pid"] and result["version"] == data["version"]
    except (OSError, ValueError, KeyError, URLError):
        return False


def spawn(port: int | None = None) -> subprocess.Popen:
    home = machine_dir()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "agent_factory.cli", "serve"]
    if port is not None:
        args.extend(["--port", str(port)])
    flags = ({
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    } if os.name == "nt" else {
        "start_new_session": True
    })
    with (logs / "launcher.log").open("ab") as stream:
        return subprocess.Popen(args,
                                stdin=subprocess.DEVNULL,
                                stdout=stream,
                                stderr=stream,
                                **flags)


def _exited(process: psutil.Process) -> bool:
    """True when the process is gone or an unreaped POSIX zombie (all handles freed)."""
    try:
        return process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def stop(data: dict[str, Any], *, stop_running: bool = False) -> None:
    process = None
    try:
        candidate = psutil.Process(data["pid"])
        if candidate.create_time() == data["create_time"]:
            process = candidate
    except psutil.NoSuchProcess:
        pass
    request(data, "/api/service/stop", {"stop_running": stop_running}, timeout=30)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        lock = FileLock(machine_dir() / "service.lock")
        try:
            if lock.acquire():
                # Releasing service.lock precedes interpreter exit. Wait for the
                # verified process to release its Windows executable/log handles.
                if process is not None:
                    try:
                        process.wait(timeout=max(0, deadline - time.monotonic()))
                    except psutil.TimeoutExpired:
                        # A POSIX parent that is not reaping (for example a foreground
                        # launcher) leaves the exited service as a zombie. It has already
                        # released the lock and every handle, so treat it as stopped.
                        if not _exited(process):
                            raise ServiceError(
                                "Service released its lock but did not exit") from None
                return
        finally:
            lock.close()
        time.sleep(0.05)
    raise ServiceError("Service did not stop within 10 seconds; see logs/service.log")


def ensure(*, port: int | None = None) -> dict[str, Any]:
    data = info()
    if healthy(data):
        if data["version"] == __version__:
            return data
        # Graceful shutdown leaves wrappers running; new service recovers them.
        stop(data)
        port = data["port"]
    child = spawn(port)
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            data = info()
            if healthy(data) and data["version"] == __version__:
                return data
            if child.poll() not in {None, 0}:
                break
            time.sleep(0.05)
        raise ServiceError(
            "Service did not become healthy within 10 seconds; see logs/launcher.log")
    finally:
        # Retain the Popen handle until exit for a failed/competing launcher.
        if child.poll() is not None:
            child.wait()


def serve(port: int | None = None) -> int:
    home = machine_dir()
    lock = FileLock(home / "service.lock")
    if not lock.acquire():
        lock.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            data = info()
            if data is not None:
                print(url(data))
                return 0
            time.sleep(0.05)
        raise ServiceError("Service lock is held but service.json is unavailable")
    listener = None
    handler = None
    try:
        selected = port if port is not None else int(os.environ.get("FACTORY_PORT", "5099"))
        if not 0 <= selected <= 65535:
            raise ValueError("port must be between 0 and 65535")
        # Bind before publishing metadata, so failed starts never advertise a URL.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", selected))
        listener.listen(128)
        port = listener.getsockname()[1]
        data = {
            "pid": os.getpid(),
            "create_time": psutil.Process().create_time(),
            "port": port,
            "token": secrets.token_urlsafe(32),
            "version": __version__
        }
        home.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            # Python's chmod cannot restrict a Windows ACL. Protect the directory
            # before writing the token; children inherit the current user's ACL.
            identity = subprocess.check_output(["whoami"], text=True).strip()
            result = subprocess.run(
                ["icacls",
                 str(home), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F"],
                capture_output=True)
            if result.returncode:
                raise ServiceError("Could not make the service directory user-only")
        else:
            home.chmod(0o700)
        run_cli.atomic_json(home / "service.json", data)
        (home / "service.json").chmod(0o600)
        logs = home / "logs"
        logs.mkdir(exist_ok=True)
        handler = RotatingFileHandler(logs / "service.log",
                                      maxBytes=5 * 1024 * 1024,
                                      backupCount=5,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger("agent_factory").addHandler(handler)
        logging.getLogger("agent_factory").setLevel(logging.INFO)
        import uvicorn
        from agent_factory.api.app import create_app
        app = create_app(port=port, token=data["token"])
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        app.state.shutdown = lambda: setattr(server, "should_exit", True)
        print(url(data), flush=True)
        server.run(sockets=[listener])
        return 0
    finally:
        if listener is not None:
            listener.close()
        metadata = home / "service.json"
        try:
            if json.loads(metadata.read_text(encoding="utf-8"))["pid"] == os.getpid():
                metadata.unlink()
        except (OSError, ValueError, KeyError):
            pass
        if handler is not None:
            logging.getLogger("agent_factory").removeHandler(handler)
            handler.close()
        lock.close()
