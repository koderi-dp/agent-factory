"""Run session 0.3 on Windows; no factory runtime imports or third-party deps.

    python spikes/windows/process_spikes.py all

Scratch directories and raw evidence stay in OS temp, with pointers under
.git/windows-spikes/. Public evidence is written once under
tests/fixtures/windows/; existing runs are kept.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Callable, TypeVar
import uuid
import zipfile


if os.name == "nt":
    if __package__:
        from .winjob_proto import Job, Process, checked, kernel32
    else:
        from winjob_proto import Job, Process, checked, kernel32


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATE = ROOT / ".git" / "windows-spikes"
FIXTURES = ROOT / "tests" / "fixtures" / "windows"
FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
    subprocess, "CREATE_NO_WINDOW", 0)
T = TypeVar("T")

SERVER = """const http = require('node:http');
const fs = require('node:fs');
const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({pid: process.pid, ppid: process.ppid}));
});
server.listen(0, '127.0.0.1', () => {
  fs.writeFileSync('server.json', JSON.stringify({
    pid: process.pid, ppid: process.ppid, port: server.address().port
  }));
});
"""

TOOL = '''import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> None:
    version = importlib.metadata.version("factory-windows-spike")
    if sys.argv[1:] == ["--version"]:
        print(version)
        return
    directory = Path(sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    worker = directory / "base_worker.py"
    shutil.copyfile(Path(__file__).with_name("base_worker.py"), worker)
    with (directory / "worker.log").open("ab") as output:
        process = subprocess.Popen(
            [sys._base_executable, str(worker), str(directory)], cwd=directory,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            close_fds=True)
    print(json.dumps({"version": version, "worker_pid": process.pid,
                      "base_executable": sys._base_executable,
                      "tool_executable": sys.executable,
                      "tool_prefix": sys.prefix, "copied_script": str(worker)}))
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def replace() -> bool:
        try:
            os.replace(temporary, path)
            return True
        except PermissionError:
            return False
    wait_for(replace, f"replacing {path}", timeout=2)


def read_json(path: Path) -> dict:
    def read() -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            return None
    return wait_for(read, f"reading {path}", timeout=2)


def redact(value: str, scratch: Path) -> str:
    # Windows APIs may expand an 8.3 temp path to its long spelling. Stdout may
    # itself contain JSON strings, so replace deeper escaping before shallower.
    for path, replacement in ((scratch, "<SCRATCH>"), (ROOT, "<SOURCE>"),
                              (Path.home(), "<HOME>")):
        for original in (str(path), str(path.resolve())):
            for depth in (3, 2, 1):
                value = value.replace(original.replace("\\", "\\" * (2 ** depth)), replacement)
            value = value.replace(original.replace("\\", "/"), replacement)
            value = value.replace(original, replacement)
    return value


def wait_for(check: Callable[[], T | None], description: str, timeout: float = 20) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError(description)


def wait_json(path: Path) -> dict:
    def check() -> dict | None:
        try:
            return read_json(path)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    return wait_for(check, f"waiting for {path}")


def capture(argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> dict:
    started = time.monotonic()
    process = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=90, creationflags=FLAGS)
    result = {"argv": argv, "exit_code": process.returncode,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "stdout": process.stdout, "stderr": process.stderr}
    # Retain command output even when a later assertion in the probe fails.
    number = len(list(cwd.glob("command-*.json"))) + 1
    write_json(cwd / f"command-{number:03}.json", result)
    require(process.returncode == 0, f"command failed: {result}")
    return result


def wrapper(directory: Path, name: str, npm: str) -> None:
    job = Job.create(name)
    job.assign_current_process()
    with (directory / "npm.log").open("ab") as output:
        process = subprocess.Popen(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", npm, "run", "dev"],
            cwd=directory, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, close_fds=True)
    write_json(directory / "wrapper.json", {
        "wrapper_pid": os.getpid(), "npm_pid": process.pid, "job_name": name,
        "handle_inheritable": job.inheritable(),
    })
    while process.poll() is None:
        if (directory / "stop").exists():
            job.terminate()  # Includes this wrapper; no cleanup/exit.json can run afterward.
        time.sleep(0.05)
    job.close()


def launch(directory: Path, name: str, npm: str) -> None:
    with (directory / "wrapper.log").open("ab") as output:
        process = subprocess.Popen(
            [sys._base_executable, str(Path(__file__).resolve()), "_wrapper",
             str(directory), name, npm], cwd=directory, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, close_fds=True, creationflags=FLAGS)
    # Intentionally do not wait for the wrapper. Its stdio has no launcher-owned pipe.
    print(json.dumps({"launcher_pid": os.getpid(), "wrapper_pid": process.pid,
                      "creationflags": FLAGS}))


def tree_probe(directory: Path, mode: str) -> dict:
    directory.mkdir()
    npm = shutil.which("npm.cmd")
    require(bool(npm), "npm.cmd is required")
    (directory / "package.json").write_text(json.dumps({
        "name": "factory-job-spike", "private": True,
        "scripts": {"dev": "node server.cjs"},
    }), encoding="utf-8")
    (directory / "server.cjs").write_text(SERVER, encoding="utf-8")
    name = "Local\\factory-spike-" + uuid.uuid4().hex
    launcher = capture([sys._base_executable, str(Path(__file__).resolve()),
                        "_launch", str(directory), name, npm], directory)
    launch_info = json.loads(launcher["stdout"])
    processes = []
    try:
        info = wait_json(directory / "wrapper.json")
        server = wait_json(directory / "server.json")
        with Job.open(name) as job:
            pids = job.process_ids()
        processes = [Process(pid) for pid in pids]
        members = [{"pid": process.pid, "image": process.image()} for process in processes]
        require(info["wrapper_pid"] in pids and server["pid"] in pids,
                "wrapper and server must be in the same job")
        require(not info["handle_inheritable"], "job handle must not leak into children")
        require(sum(Path(member["image"]).name.lower() == "node.exe"
                    for member in members) >= 2, "npm and HTTP server must both be node.exe")
        require(all(not process.exited() for process in processes), "tree died with launcher")
        # The launcher has exited; independently exercise the server and wait again.
        port = server["port"]

        def http_response() -> str:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                return connection.recv(4096).decode("utf-8")
        require("200 OK" in http_response(), "server did not survive launcher exit")
        time.sleep(0.5)
        require(all(not process.exited() for process in processes), "tree did not stay alive")
        started = time.monotonic()
        if mode == "terminate":
            with Job.open(name) as job:
                job.terminate()
        elif mode == "stop":
            (directory / "stop").touch()
        else:
            # No external Job handles remain: wrapper exit closes the last one.
            from ctypes import wintypes
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess.restype = wintypes.BOOL
            handle = checked(kernel32.OpenProcess(1, False, info["wrapper_pid"]))
            try:
                checked(kernel32.TerminateProcess(handle, 74))
            finally:
                checked(kernel32.CloseHandle(handle))
        wait_for(lambda: all(process.exited() for process in processes), "tree termination")
        # Actually rebind/listen, rather than merely infer release from process death.
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen()
        result = {"mode": mode, "launcher": launcher, "wrapper": info, "server": server,
                  "members": members, "launcher_exited_tree_survived": True,
                  "http_after_launcher_exit": "200 OK", "all_members_exited": True,
                  "port_rebound": True,
                  "stop_elapsed_seconds": round(time.monotonic() - started, 3)}
        write_json(directory / "result.json", result)
        return result
    finally:
        try:
            with Job.open(name) as job:
                job.terminate()
        except OSError as error:
            if error.winerror != 2:  # Already destroyed; do not mask other cleanup failures.
                raise
        finally:
            for process in processes:
                process.close()


def build_wheel(directory: Path, version: str) -> Path:
    """Build a tiny dependency-free wheel with stdlib, avoiding a build backend install."""
    distribution = "factory_windows_spike"
    info = f"{distribution}-{version}.dist-info"
    files = {
        f"{distribution}/__init__.py": TOOL,
        f"{distribution}/base_worker.py": (HERE / "base_worker.py").read_text(encoding="utf-8"),
        f"{info}/METADATA": ("Metadata-Version: 2.1\nName: factory-windows-spike\n"
                              f"Version: {version}\nRequires-Python: >=3.11\n"),
        f"{info}/WHEEL": ("Wheel-Version: 1.0\nGenerator: session-0.3-stdlib\n"
                           "Root-Is-Purelib: true\nTag: py3-none-any\n"),
        f"{info}/entry_points.txt": ("[console_scripts]\n"
                                      "factory-windows-spike = factory_windows_spike:main\n"),
    }
    record = io.StringIO()
    path = directory / f"{distribution}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as wheel:
        for name, content in files.items():
            data = content.encode("utf-8")
            wheel.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.write(f"{name},sha256={digest},{len(data)}\n")
        record.write(f"{info}/RECORD,,\n")
        wheel.writestr(f"{info}/RECORD", record.getvalue())
    return path


def uv_probe(directory: Path) -> dict:
    directory.mkdir()
    uv = shutil.which("uv")
    require(bool(uv), "uv is required")
    environment = os.environ.copy()
    overrides = {"UV_TOOL_DIR": str(directory / "tools"),
                 "UV_TOOL_BIN_DIR": str(directory / "bin"), "UV_NO_CONFIG": "1"}
    environment.update(overrides)
    first = build_wheel(directory, "0.0.1")
    second = build_wheel(directory, "0.0.2")
    base_argv = [uv, "tool", "install", "--offline", "--no-index",
                 "--python", sys._base_executable]
    install = capture(base_argv + [str(first)], directory, environment)
    tool = str(directory / "bin" / "factory-windows-spike.exe")
    runtime = directory / "runtime"
    launch_result = capture([tool, str(runtime)], directory, environment)
    launched = json.loads(launch_result["stdout"])
    worker = Process(launched["worker_pid"])
    try:
        before = wait_json(runtime / "heartbeat.json")
        require(Path(before["executable"]).resolve() == Path(launched["base_executable"]).resolve(),
                "worker did not use the base interpreter")
        tools = (directory / "tools").resolve()
        require(not Path(before["executable"]).resolve().is_relative_to(tools),
                "worker executable is inside the tool venv")
        require(not Path(before["prefix"]).resolve().is_relative_to(tools),
                "worker prefix is inside the tool venv")
        require(Path(before["script"]).resolve().is_relative_to(runtime.resolve()),
                "script was not copied to temp")
        replacement = capture(base_argv + ["--force", str(second)], directory, environment)
        version = capture([tool, "--version"], directory, environment)
        require(version["stdout"].strip() == "0.0.2", "new tool version was not installed")
        post_install_count = read_json(runtime / "heartbeat.json")["count"]
        after = wait_for(lambda: (current if (current := read_json(runtime / "heartbeat.json"))[
            "count"] > post_install_count else None), "worker heartbeat after reinstall")
        require(after["pid"] == before["pid"] and not worker.exited(),
                "worker did not survive tool replacement")
        result = {"env_overrides": overrides, "install": install, "launch": launch_result,
                  "launched": launched, "before": before, "force_install": replacement,
                  "new_version": version, "after": after, "worker_image": worker.image(),
                  "same_worker_alive": True, "no_file_lock_failure": True}
        (runtime / "stop").touch()
        wait_for(worker.exited, "base worker cleanup")
        result["worker_stopped"] = True
        write_json(directory / "result.json", result)
        return result
    finally:
        (runtime / "stop").touch()
        try:
            wait_for(worker.exited, "base worker cleanup")
        finally:
            worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["all", "tree", "uv", "_launch", "_wrapper"])
    parser.add_argument("internal", nargs="*")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("live process spikes require Windows")
    if args.case in ("_launch", "_wrapper"):
        directory, name, npm = args.internal
        (launch if args.case == "_launch" else wrapper)(Path(directory), name, npm)
        return
    cases = ["tree", "uv"] if args.case == "all" else [args.case]
    # Check all targets before starting any experiment.
    for case in cases:
        if (FIXTURES / f"{case}.json").exists():
            raise FileExistsError(f"Preserving existing Windows/{case}.json evidence")
    STATE.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="factory-windows-spike-"))
    write_json(STATE / f"{scratch.name}.json", {"scratch": str(scratch)})
    print(f"Scratch and raw logs: {scratch}", flush=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for case in cases:
        result = {"platform": platform.platform(), "python": sys.version,
                  "base_executable": sys._base_executable,
                  "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            if case == "tree":
                result["node_version"] = capture([shutil.which("node"), "--version"], scratch)
                result["npm_version"] = capture([os.environ.get("COMSPEC", "cmd.exe"),
                                                 "/d", "/c", shutil.which("npm.cmd"),
                                                 "--version"], scratch)
                result["cases"] = [tree_probe(scratch / ("tree-" + mode), mode)
                                   for mode in ("terminate", "stop", "close")]
            else:
                result["uv_version"] = capture([shutil.which("uv"), "--version"], scratch)
                result["case"] = uv_probe(scratch / "uv")
        except BaseException:
            (scratch / f"{case}-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        write_json(scratch / f"{case}.json", result)
        serialized = redact(json.dumps(result, indent=2), scratch)
        (FIXTURES / f"{case}.json").write_text(serialized + "\n", encoding="utf-8")
        print(f"{case}: PASS (evidence: tests/fixtures/windows/{case}.json)", flush=True)


if __name__ == "__main__":
    main()
