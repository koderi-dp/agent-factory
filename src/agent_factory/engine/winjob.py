"""Detached wrapper launch and process ownership helpers (Windows and POSIX)."""

import hashlib
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import signal
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
    options = ({
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    } if os.name == "nt" else {
        "start_new_session": True
    })
    # -I -S also excludes user site hooks and PYTHONPATH from this stdlib process.
    return subprocess.Popen([
        sys._base_executable, "-I", "-S",
        str(runtime), "--attempt-dir",
        str(directory), "--token", token
    ],
                            cwd=directory,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                            **options)


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
    return True


def alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def contains_process(attempt_dir: str | Path, token: str, candidate: psutil.Process) -> bool:
    """Recognise contained servers even after their immediate parent has exited."""
    root = owned_process(attempt_dir, token)
    if root is None or not alive(candidate):
        return False
    try:
        if os.name != "nt":
            return os.getpgid(root.pid) == root.pid == os.getpgid(
                candidate.pid) and alive(candidate)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, restype, argtypes in (
            ("OpenJobObjectW", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]),
            ("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
            ("IsProcessInJob", wintypes.BOOL,
             [wintypes.HANDLE, wintypes.HANDLE,
              ctypes.POINTER(wintypes.BOOL)]),
            ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
        ):
            function = getattr(kernel, name)
            function.restype, function.argtypes = restype, argtypes
        # QUERY only, non-inheritable, held just for these membership checks.
        # https://learn.microsoft.com/windows/win32/api/jobapi/nf-jobapi-isprocessinjob
        job = kernel.OpenJobObjectW(4, False, run_cli.job_name(token))
        if not job:
            return False  # old unnamed wrappers still use the psutil tree path
        try:
            for process in (root, candidate):
                handle = kernel.OpenProcess(0x1000, False, process.pid)
                if not handle:
                    return False
                try:
                    member = wintypes.BOOL()
                    if not kernel.IsProcessInJob(
                            handle, job,
                            ctypes.byref(member)) or not member.value or not alive(process):
                        return False
                finally:
                    kernel.CloseHandle(handle)
            return True
        finally:
            kernel.CloseHandle(job)
    except (OSError, psutil.Error):
        return False


def owned_tree(attempt_dir: str | Path, token: str) -> list[psutil.Process]:
    """Snapshot identities only after proving wrapper ownership."""
    process = owned_process(attempt_dir, token)
    if process is None:
        return []
    try:
        children = process.children(recursive=True)
        # Cache creation times before any signal; psutil's signal methods check
        # these identities again, so a reused child PID is never a target.
        for child in children:
            child.create_time()
        if owned_process(attempt_dir, token) is None:
            return []
        return [process, *children]
    except psutil.Error:
        return [process]


def signal_tree(attempt_dir: str | Path, token: str, members: list[psutil.Process], *,
                kill: bool) -> None:
    """Signal a verified group, or individual identities captured from its tree.

    The latter also covers descendants orphaned while stopping. Never discover
    targets by port number or use taskkill on an unverified numeric PID.
    """
    process = owned_process(attempt_dir, token)
    if os.name != "nt" and process is not None:
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL if kill else signal.SIGTERM)
        except ProcessLookupError:
            pass
    # A descendant can create another session (for example a browser launcher).
    # Its captured identity still belongs to this tree even outside the group.
    for member in reversed(members):
        try:
            if alive(member):
                member.kill() if kill else member.terminate()
        except psutil.NoSuchProcess:
            pass
