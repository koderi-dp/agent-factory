"""Standalone attempt wrapper (plan §3.2); only the standard library is available.

The engine copies this file out of its installation and runs it with base Python.
command.json holds argv, cwd, env additions, and an optional stdin path (relative
paths are relative to the attempt directory). exit.json is the CLI's exit status;
a stopped or crashed wrapper deliberately leaves no exit.json.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


POLL_INTERVAL = 0.05
STOP_GRACE_SECONDS = 10


def job_name(token: str) -> str:
    return "Local\\agent-factory-" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Publish complete bytes, retrying transient Windows sharing violations."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(POLL_INTERVAL)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: object) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WindowsJob:
    """The wrapper holds the lifetime, non-inheritable KILL_ON_JOB_CLOSE handle."""

    def __init__(self, token: str) -> None:
        self.name = job_name(token)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, restype, argtypes in (
            ("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
            ("SetInformationJobObject", wintypes.BOOL,
             [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
            ("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
            ("GetCurrentProcess", wintypes.HANDLE, []),
            ("GetProcessTimes", wintypes.BOOL,
             [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4),
            ("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
            ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
        ):
            function = getattr(self.kernel32, name)
            function.restype = restype
            function.argtypes = argtypes
        self.handle = 0

    @staticmethod
    def _checked(value: int) -> int:
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def create_time(self) -> float:
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        self._checked(self.kernel32.GetProcessTimes(
            self.kernel32.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(ended),
            ctypes.byref(kernel), ctypes.byref(user)))
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        # Match psutil's C conversion: round the integer to double BEFORE
        # dividing. Python's int/int division can differ by one float ULP.
        return float(ticks - 116444736000000000) / 10_000_000

    def contain(self) -> None:
        # NULL security attributes make this handle non-inheritable. Descendants
        # inherit membership, not a handle that would keep the job alive.
        self.handle = self._checked(self.kernel32.CreateJobObjectW(None, self.name))
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: never join another job
            raise OSError("An attempt Job Object with this owner token already exists")
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        self._checked(self.kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        self._checked(self.kernel32.AssignProcessToJobObject(
            self.handle, self.kernel32.GetCurrentProcess()))

    def terminate(self) -> None:
        # Includes the wrapper: execution does not return from this call.
        self._checked(self.kernel32.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle:
            self._checked(self.kernel32.CloseHandle(self.handle))
            self.handle = 0


def posix_create_time() -> float:
    """Read the kernel start time with the same resolution as psutil."""
    if sys.platform == "linux":
        # comm may contain spaces and parentheses; fields after its LAST ')' are
        # unambiguous. starttime is field 22, or index 19 after comm.
        fields = Path("/proc/self/stat").read_bytes().rsplit(b")", 1)[1].split()
        ticks = int(fields[19])
        for line in Path("/proc/stat").read_bytes().splitlines():
            if line.startswith(b"btime "):
                return ticks / os.sysconf("SC_CLK_TCK") + int(line.split()[1])
        raise RuntimeError("/proc/stat has no boot time")
    if sys.platform == "darwin":
        # Apple's bsd/sys/proc_info.h: PROC_PIDTBSDINFO / struct proc_bsdinfo.
        # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h
        class BsdInfo(ctypes.Structure):
            _fields_ = [("ids", ctypes.c_uint32 * 12),
                        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
                        ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                        ("pjobc", ctypes.c_uint32), ("tdev", ctypes.c_uint32),
                        ("tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
                        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                        ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.restype = ctypes.c_int
        info = BsdInfo()
        if libproc.proc_pidinfo(os.getpid(), 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            raise OSError(ctypes.get_errno(), "proc_pidinfo")
        return info.start_sec + info.start_usec / 1_000_000
    raise RuntimeError(f"Unsupported process start-time platform: {sys.platform}")


def read_command(directory: Path) -> dict:
    command = json.loads((directory / "command.json").read_text(encoding="utf-8"))
    if not isinstance(command, dict):
        raise ValueError("command.json must be an object")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
        raise ValueError("command.argv must be a nonempty list of strings")
    if not argv[0]:
        raise ValueError("command.argv[0] must not be empty")
    if not isinstance(command.get("cwd"), str) or not command["cwd"]:
        raise ValueError("command.cwd must be a path")
    additions = command.get("env", {})
    if not isinstance(additions, dict) or not all(
            isinstance(key, str) and key and "=" not in key and "\0" not in key
            and isinstance(value, str) and "\0" not in value for key, value in additions.items()):
        raise ValueError("command.env must contain string environment additions")
    if command.get("stdin") is not None and not isinstance(command["stdin"], str):
        raise ValueError("command.stdin must be a path or null")
    return command


def run(directory: Path, token: str) -> int:
    job = WindowsJob(token) if os.name == "nt" else None
    created = job.create_time() if job is not None else posix_create_time()
    # This MUST be the first artifact: even a containment/command failure is an
    # identifiable, lost attempt, never evidence that a spawn did not happen.
    atomic_json(directory / "proc.json", {
        "wrapper_pid": os.getpid(), "wrapper_create_time": created, "token": token,
    })
    stopping = False
    contained = False

    def on_term(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    try:
        if job is not None:
            job.contain()
        else:
            if os.getpgrp() != os.getpid() or os.getsid(0) != os.getpid():
                raise RuntimeError("The wrapper must be launched in its own POSIX session")
            signal.signal(signal.SIGTERM, on_term)
        contained = True
        if (directory / "stop").exists():
            return 0
        with ExitStack() as stack:
            output = stack.enter_context((directory / "events.jsonl").open("ab", buffering=0))
            command = read_command(directory)
            cwd = directory / command["cwd"]
            stdin = (stack.enter_context((directory / command["stdin"]).open("rb"))
                     if command.get("stdin") is not None else subprocess.DEVNULL)
            process = subprocess.Popen(
                command["argv"], cwd=cwd, env={**os.environ, **command.get("env", {})},
                stdin=stdin, stdout=output, stderr=subprocess.STDOUT, close_fds=True)
            while True:
                if stopping or (directory / "stop").exists():
                    if job is not None:
                        job.terminate()
                    else:
                        # Keep the wrapper alive to enforce SIGKILL even when a
                        # descendant ignores SIGTERM. The final group kill also
                        # covers orphaned grandchildren after the CLI exits.
                        os.killpg(os.getpid(), signal.SIGTERM)
                        deadline = time.monotonic() + STOP_GRACE_SECONDS
                        while time.monotonic() < deadline:
                            process.poll()  # reap the CLI if it has exited
                            time.sleep(POLL_INTERVAL)
                    return 0
                code = process.poll()
                if code is not None:
                    atomic_json(directory / "exit.json", {
                        "exit_code": code, "ended_at": datetime.now(timezone.utc).isoformat(),
                    })
                    return 0
                time.sleep(POLL_INTERVAL)
    except Exception as error:
        # Infrastructure failures are lost attempts (§6.4), not normal CLI exits.
        with (directory / "events.jsonl").open("ab", buffering=0) as output:
            output.write((json.dumps({"type": "wrapper.error", "message": str(error)}) + "\n").encode("utf-8"))
        return 1
    finally:
        if job is not None:
            job.close()  # normal completion also removes leftover dev servers
        elif contained:
            # Like KILL_ON_JOB_CLOSE, clean up ALL remaining members on exit.
            # The engine uses exit.json, not the wrapper's OS return code.
            os.killpg(os.getpid(), signal.SIGKILL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    if not args.token:
        parser.error("--token must not be empty")
    return run(args.attempt_dir.resolve(), args.token)


if __name__ == "__main__":
    raise SystemExit(main())
