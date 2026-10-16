"""Real detached wrappers and descendant servers on both Windows and POSIX."""

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import Mock
import uuid

import psutil
import pytest

from agent_factory import run_cli
from agent_factory.engine import winjob

FAKE_CLI = Path(__file__).with_name("fake_cli.py").resolve()


def wait_for(check: Callable, timeout: float = 15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError(f"Timed out waiting for {check}")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError):
        return None


def alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def port_released(port: int) -> bool:
    try:
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen()
        return True
    except OSError:
        return False


class Attempt:

    def __init__(self, root: Path, scenario: dict) -> None:
        self.directory = root / f"attempt č {uuid.uuid4().hex}"
        self.directory.mkdir()
        self.worktree = self.directory / "worktree č"
        self.worktree.mkdir()
        self.token = uuid.uuid4().hex
        self.process = None
        self.identity = None
        self.handle = None
        self.report = self.worktree / "report.md"
        self.scenario = {"report_path": str(self.report), **scenario}
        run_cli.atomic_json(self.directory / "scenario.json", self.scenario)
        (self.directory / "prompt.md").write_bytes("prompt č\n".encode("utf-8"))
        self.command = {
            "argv": [
                sys._base_executable, "-I", "-S",
                str(FAKE_CLI), "--scenario",
                str(self.directory / "scenario.json")
            ],
            "cwd":
            "worktree č",
            "env": {
                "FACTORY_TEST_ADDITION": "added č"
            },
            "stdin":
            "prompt.md",
        }
        run_cli.atomic_json(self.directory / "command.json", self.command)

    def launch(self, detached: bool = False) -> None:
        if detached:
            code = ("import os; from agent_factory.engine.winjob import spawn_wrapper; "
                    f"p = spawn_wrapper({str(self.directory)!r}, {self.token!r}); "
                    "print(p.pid, flush=True); os._exit(0)")
            launcher = subprocess.run([sys.executable, "-c", code],
                                      capture_output=True,
                                      text=True,
                                      timeout=15)
            assert launcher.returncode == 0, launcher.stderr
            self.process = psutil.Process(int(launcher.stdout.strip()))
        else:
            self.handle = winjob.spawn_wrapper(self.directory, self.token)
            self.process = psutil.Process(self.handle.pid)
        self.identity = self.process.create_time()

    def info(self, name: str) -> dict:
        return wait_for(lambda: read_json(self.directory / name))

    def members(self) -> tuple[list[psutil.Process], int]:
        ready = self.info("ready.json")
        server = self.info("server.json")
        assert server["parent_pid"] == ready["child_pid"]
        return [
            self.process,
            psutil.Process(ready["pid"]),
            psutil.Process(ready["child_pid"]),
            psutil.Process(server["pid"])
        ], server["port"]

    def cleanup(self) -> None:
        if self.process is not None and alive(self.process):
            assert self.process.create_time() == self.identity
            if os.name == "nt":
                self.process.kill()  # closing the wrapper's job handle kills its descendants
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
        if self.handle is not None:
            self.handle.wait(timeout=15)


@pytest.fixture
def attempt_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine č"))
    monkeypatch.setenv("FACTORY_TEST_INHERITED", "inherited")
    attempts = []

    def prepare(**scenario):
        attempt = Attempt(tmp_path, scenario)
        attempts.append(attempt)
        return attempt

    yield prepare
    for attempt in attempts:
        attempt.cleanup()


def test_runtime_copy_is_atomic_content_addressed_and_keeps_old_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine č"))
    original = Path(run_cli.__file__).read_bytes()
    first = winjob.install_wrapper()
    assert first.name == f"run_cli-{hashlib.sha256(original).hexdigest()[:8]}.py"
    assert first.read_bytes() == original
    timestamp = first.stat().st_mtime_ns
    assert winjob.install_wrapper() == first
    assert first.stat().st_mtime_ns == timestamp
    source = tmp_path / "new_wrapper.py"
    source.write_bytes(original + b"\n# new release\n")
    monkeypatch.setattr(run_cli, "__file__", str(source))
    second = winjob.install_wrapper()
    assert second != first
    assert first.read_bytes() == original
    assert second.read_bytes() == source.read_bytes()
    second.write_bytes(b"damaged")
    with pytest.raises(RuntimeError, match="damaged"):
        winjob.install_wrapper()


def test_atomic_write_retries_sharing_violation_and_never_publishes_partial_bytes(
        tmp_path, monkeypatch):
    target = tmp_path / "proc.json"
    target.write_bytes(b"old")
    original_replace = os.replace
    calls = []

    def replace(source, destination):
        assert Path(source).parent == target.parent
        assert Path(source).read_bytes() == b"complete new value"
        assert target.read_bytes() == b"old"
        calls.append(source)
        if len(calls) == 1:
            raise PermissionError("sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(run_cli.os, "replace", replace)
    run_cli.atomic_write(target, b"complete new value")
    assert target.read_bytes() == b"complete new value"
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == [target]


def test_windows_creation_time_matches_psutil_double_rounding():
    # Captured from a real wrapper: int/int division gave 1792101677.6542678,
    # making a live, owned wrapper fail the exact creation-time check.
    ticks = 116444736000000000 + 17921016776542677
    job = run_cli.WindowsJob.__new__(run_cli.WindowsJob)
    job.kernel32 = Mock()

    def get_times(process, created, ended, kernel, user):
        created._obj.dwHighDateTime = ticks >> 32
        created._obj.dwLowDateTime = ticks & 0xFFFFFFFF
        return 1

    job.kernel32.GetProcessTimes.side_effect = get_times
    assert job.create_time() == 1792101677.6542675


@pytest.mark.parametrize("exit_code", [0, 23])
def test_base_wrapper_captures_live_output_stdin_env_cwd_report_and_exit(
        attempt_factory, exit_code):
    attempt = attempt_factory(sleep=0.75,
                              exit=exit_code,
                              report="VERDICT: APPROVE\n",
                              events=[{
                                  "type": "fake.started",
                                  "text": "č"
                              }],
                              stderr='{"stream":"stderr"}\r\n',
                              capture_env=["FACTORY_TEST_INHERITED", "FACTORY_TEST_ADDITION"])
    started = datetime.now(timezone.utc)
    attempt.launch()
    ready = attempt.info("ready.json")
    assert ready["stdin"] == "prompt č\n"
    assert Path(ready["cwd"]).resolve() == attempt.worktree.resolve()
    assert ready["env"] == {
        "FACTORY_TEST_INHERITED": "inherited",
        "FACTORY_TEST_ADDITION": "added č"
    }
    proc = ready["proc"]
    assert proc == attempt.info("proc.json")
    assert set(proc) == {"wrapper_pid", "wrapper_create_time", "token"}
    assert proc["wrapper_pid"] == attempt.process.pid
    assert proc["wrapper_create_time"] == attempt.process.create_time()
    assert proc["token"] == attempt.token
    assert winjob.owned_process(attempt.directory, attempt.token) == attempt.process
    if sys.platform != "darwin":
        # macOS framework stubs exec the real binary, so process.exe() is the
        # framework binary rather than sys._base_executable; the pid/create_time
        # checks above already confirm the wrapper is the launched process.
        assert Path(attempt.process.exe()).resolve() == Path(sys._base_executable).resolve()
    argv = attempt.process.cmdline()
    assert argv[1:3] == ["-I", "-S"]
    assert Path(argv[3]).parent == winjob.install_wrapper().parent
    output = attempt.directory / "events.jsonl"
    wait_for(lambda: output.read_bytes()
             if output.exists() and b"stderr" in output.read_bytes() else None)
    assert not (attempt.directory / "exit.json").exists()
    assert output.read_bytes(
    ) == '{"type": "fake.started", "text": "č"}\n{"stream":"stderr"}\r\n'.encode("utf-8")
    result = attempt.info("exit.json")
    assert set(result) == {"exit_code", "ended_at"}
    assert result["exit_code"] == exit_code
    assert datetime.fromisoformat(result["ended_at"]) >= started
    assert attempt.report.read_text(encoding="utf-8") == "VERDICT: APPROVE\n"
    wait_for(lambda: not alive(attempt.process))
    assert winjob.owned_process(attempt.directory, attempt.token) is None
    assert len(list((attempt.directory / "invocations").iterdir())) == 1


@pytest.mark.parametrize("ignore_sigterm", [False, True])
def test_stop_flag_kills_whole_tree_releases_port_and_preserves_foreign_listener(
        attempt_factory, ignore_sigterm):
    attempt = attempt_factory(hang=True,
                              spawn_child_listening_on_port=True,
                              ignore_sigterm=ignore_sigterm)
    attempt.launch()
    members, port = attempt.members()
    assert all(alive(member) for member in members)
    assert not port_released(port)
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        foreign_port = foreign.getsockname()[1]
        assert not winjob.request_stop(attempt.directory, attempt.token + "wrong")
        info = attempt.info("proc.json")
        run_cli.atomic_json(attempt.directory / "proc.json", {
            **info, "wrapper_create_time": info["wrapper_create_time"] - 1
        })
        assert not winjob.request_stop(attempt.directory, attempt.token)
        assert not (attempt.directory / "stop").exists()
        assert all(alive(member) for member in members)
        run_cli.atomic_json(attempt.directory / "proc.json", info)
        assert winjob.request_stop(attempt.directory, attempt.token)
        wait_for(lambda: all(not alive(member) for member in members))
        wait_for(lambda: port_released(port))
        assert not port_released(foreign_port)
        assert foreign.getsockname()[1] == foreign_port
    assert not (attempt.directory / "exit.json").exists()
    assert not attempt.report.exists()


def test_wrapper_tree_survives_launcher_exiting(attempt_factory):
    attempt = attempt_factory(hang=True, spawn_child_listening_on_port=True)
    attempt.launch(detached=True)  # launcher has already os._exit(0)'d
    members, port = attempt.members()
    time.sleep(0.2)
    assert all(alive(member) for member in members)
    assert not port_released(port)
    assert winjob.owned_process(attempt.directory, attempt.token) == attempt.process
    assert winjob.request_stop(attempt.directory, attempt.token)
    wait_for(lambda: all(not alive(member) for member in members))
    wait_for(lambda: port_released(port))


def test_normal_exit_cleans_up_leftover_grandchild_server(attempt_factory):
    attempt = attempt_factory(sleep=1, exit=7, spawn_child_listening_on_port=True)
    attempt.launch()
    members, port = attempt.members()
    assert attempt.info("exit.json")["exit_code"] == 7
    wait_for(lambda: all(not alive(member) for member in members))
    wait_for(lambda: port_released(port))


@pytest.mark.skipif(os.name != "nt", reason="KILL_ON_JOB_CLOSE is Windows-specific")
def test_wrapper_crash_closes_only_job_handle_and_kills_descendants(attempt_factory):
    attempt = attempt_factory(hang=True, spawn_child_listening_on_port=True)
    attempt.launch()
    members, port = attempt.members()
    attempt.process.kill()
    wait_for(lambda: all(not alive(member) for member in members))
    wait_for(lambda: port_released(port))
    assert not (attempt.directory / "exit.json").exists()


def test_preexisting_stop_prevents_cli_invocation(attempt_factory):
    attempt = attempt_factory(hang=True)
    run_cli.atomic_write(attempt.directory / "stop", b"")
    attempt.launch()
    attempt.info("proc.json")
    wait_for(lambda: not alive(attempt.process))
    assert not (attempt.directory / "invocations").exists()
    assert not (attempt.directory / "exit.json").exists()


def test_invalid_command_is_identifiable_lost_attempt_without_cli_exit(attempt_factory):
    attempt = attempt_factory()
    run_cli.atomic_json(attempt.directory / "command.json", {**attempt.command, "argv": []})
    attempt.launch()
    attempt.info("proc.json")
    wait_for(lambda: not alive(attempt.process))
    events = (attempt.directory / "events.jsonl").read_text(encoding="utf-8")
    assert json.loads(events)["type"] == "wrapper.error"
    assert "command.argv" in events
    assert not (attempt.directory / "exit.json").exists()
    assert not (attempt.directory / "invocations").exists()


def test_proc_is_published_even_before_containment_failure(attempt_factory, monkeypatch):
    attempt = attempt_factory()

    def fail(*args):
        assert read_json(attempt.directory / "proc.json")["token"] == attempt.token
        assert not (attempt.directory / "events.jsonl").exists()
        raise OSError("containment denied")

    if os.name == "nt":
        monkeypatch.setattr(run_cli.WindowsJob, "contain", fail)
    else:
        monkeypatch.setattr(run_cli.os, "getpgrp", fail)
    assert run_cli.run(attempt.directory, attempt.token) == 1
    assert "containment denied" in (attempt.directory / "events.jsonl").read_text()
    assert not (attempt.directory / "exit.json").exists()


@pytest.mark.parametrize("info", [
    None, [], {}, {
        "wrapper_pid": True
    }, {
        "wrapper_pid": -1,
        "wrapper_create_time": 1,
        "token": "owner"
    }, {
        "wrapper_pid": 1,
        "wrapper_create_time": float("nan"),
        "token": "owner"
    }
])
def test_malformed_or_missing_process_identity_never_allows_stop(tmp_path, info):
    if info is not None:
        run_cli.atomic_json(tmp_path / "proc.json", info)
    assert winjob.owned_process(tmp_path, "owner") is None
    assert not winjob.request_stop(tmp_path, "owner")
    assert not (tmp_path / "stop").exists()


def test_matching_pid_and_creation_time_without_cmdline_token_never_allows_stop(tmp_path):
    process = psutil.Process()
    run_cli.atomic_json(tmp_path / "proc.json", {
        "wrapper_pid": process.pid,
        "wrapper_create_time": process.create_time(),
        "token": "owner",
    })
    assert winjob.owned_process(tmp_path, "owner") is None
    assert not winjob.request_stop(tmp_path, "owner")


def test_launch_uses_base_interpreter_and_detached_platform_flags(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.py"
    monkeypatch.setattr(winjob, "install_wrapper", lambda: runtime)
    popen = Mock()
    monkeypatch.setattr(winjob.subprocess, "Popen", popen)
    winjob.spawn_wrapper(tmp_path, "owner")
    argv = popen.call_args.args[0]
    options = popen.call_args.kwargs
    assert argv == [
        sys._base_executable, "-I", "-S",
        str(runtime), "--attempt-dir",
        str(tmp_path.resolve()), "--token", "owner"
    ]
    for stream in ("stdin", "stdout", "stderr"):
        assert options[stream] == subprocess.DEVNULL
    assert options["close_fds"]
    if os.name == "nt":
        assert options[
            "creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        assert options["start_new_session"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX escaped-session cleanup")
def test_normal_completion_cleans_escaped_orphan_server_before_exit(attempt_factory):
    attempt = attempt_factory(spawn_child_listening_on_port=True,
                              server_new_session=True,
                              orphan_server=True,
                              sleep=1)
    attempt.launch()
    server = attempt.info("server.json")
    process = psutil.Process(server["pid"])
    try:
        assert not port_released(server["port"])
        attempt.info("exit.json")
        assert not alive(process)
        assert port_released(server["port"])
    finally:
        if alive(process):
            process.kill()


@pytest.mark.parametrize("reused", [False, True])
def test_posix_descendant_scan_rejects_pid_reuse_during_ownership_read(monkeypatch, reused):
    entry = Mock()
    entry.name = "123"
    proc = Mock()
    proc.iterdir.return_value = [entry]
    environment = Mock()
    environment.read_bytes.return_value = b"FACTORY_ATTEMPT_TOKEN=owner\0"
    monkeypatch.setattr(run_cli.sys, "platform", "linux")
    monkeypatch.setattr(run_cli, "Path", Mock(side_effect=[proc, environment]))
    monkeypatch.setattr(run_cli, "posix_create_time", Mock(side_effect=[10, 11 if reused else 10]))
    assert run_cli.posix_descendants("owner") == ({} if reused else {123: 10})
