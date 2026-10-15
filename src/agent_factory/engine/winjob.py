"""Detached wrapper launch and process ownership helpers (Windows and POSIX)."""

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import psutil

from agent_factory import run_cli
from agent_factory.paths import machine_dir


def install_wrapper() -> Path:
    """Keep immutable, content-addressed runtime copies outside the tool venv."""
    source = Path(run_cli.__file__).read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:8]
    runtime = machine_dir() / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / f"run_cli-{digest}.py"
    if target.exists():
        if target.read_bytes() != source:
            raise RuntimeError(f"Wrapper runtime hash collision or damaged copy: {target}")
    else:
        run_cli.atomic_write(target, source)
    return target


def spawn_wrapper(attempt_dir: str | Path, token: str) -> subprocess.Popen:
    """Launch without launcher-owned pipes or any dependency on its lifetime."""
    if not token or "\0" in token:
        raise ValueError("An owner token is required")
    directory = Path(attempt_dir).resolve()
    runtime = install_wrapper()
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    # -I -S also excludes user site hooks and PYTHONPATH from this stdlib process.
    return subprocess.Popen(
        [sys._base_executable, "-I", "-S", str(runtime),
         "--attempt-dir", str(directory), "--token", token], cwd=directory,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, **options)


def owned_process(attempt_dir: str | Path, token: str) -> psutil.Process | None:
    """Require the row's token, kernel creation time, and exact cmdline token."""
    try:
        info = json.loads((Path(attempt_dir) / "proc.json").read_text(encoding="utf-8"))
        if not isinstance(info, dict) or not token or info.get("token") != token:
            return None
        pid, created = info.get("wrapper_pid"), info.get("wrapper_create_time")
        if (type(pid) is not int or pid <= 0 or type(created) not in (int, float)
                or not math.isfinite(created)):
            return None
        process = psutil.Process(pid)
        if process.create_time() != created or not process.is_running():
            return None
        argv = process.cmdline()
        if not any(first == "--token" and second == token for first, second in zip(argv, argv[1:])):
            return None
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (OSError, ValueError, psutil.Error):
        return None


def request_stop(attempt_dir: str | Path, token: str) -> bool:
    """Ask a verified wrapper to terminate its own tree, without signalling a PID."""
    if owned_process(attempt_dir, token) is None:
        return False
    run_cli.atomic_write(Path(attempt_dir) / "stop", b"")
    # TODO (plan §9, session 1.7): supervisor fallback for an unresponsive wrapper;
    # POSIX signal escalation, timeout handling, and stopped-state persistence.
    return True
