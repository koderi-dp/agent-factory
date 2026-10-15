"""Dependency-free CLI stand-in; session 1.4's process/output scenarios (§16.1).

Use --scenario <json> or FACTORY_FAKE_SCENARIO. Later adapter sessions can add
provider-specific event and auth scenarios without invoking a real provider.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def ignore_term(enabled: bool) -> None:
    if enabled and os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)


def server(path: Path, ignore: bool) -> None:
    ignore_term(ignore)
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        write_json(path, {"pid": os.getpid(), "parent_pid": os.getppid(),
                          "port": listener.getsockname()[1]})
        while True:
            connection, _ = listener.accept()
            connection.close()


def child(path: Path, ignore: bool) -> None:
    ignore_term(ignore)
    subprocess.Popen([sys._base_executable, "-I", "-S", str(Path(__file__).resolve()),
                      "--server", str(path), "--ignore-term", str(int(ignore))],
                     stdin=subprocess.DEVNULL, close_fds=True)
    while True:
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, default=os.environ.get("FACTORY_FAKE_SCENARIO"))
    parser.add_argument("--child", type=Path)
    parser.add_argument("--server", type=Path)
    parser.add_argument("--ignore-term", type=int, default=0)
    args = parser.parse_args()
    if args.server:
        server(args.server, bool(args.ignore_term))
    if args.child:
        child(args.child, bool(args.ignore_term))
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    ignore_term(scenario.get("ignore_sigterm", False))
    directory = args.scenario.parent
    markers = Path(scenario.get("invocation_marker", directory / "invocations"))
    markers.mkdir(parents=True, exist_ok=True)
    write_json(markers / f"{uuid.uuid4().hex}.json", {"pid": os.getpid()})
    child_pid = None
    if scenario.get("spawn_child_listening_on_port"):
        process = subprocess.Popen(
            [sys._base_executable, "-I", "-S", str(Path(__file__).resolve()),
             "--child", str(directory / "server.json"),
             "--ignore-term", str(int(scenario.get("ignore_sigterm", False)))],
            stdin=subprocess.DEVNULL, close_fds=True)
        child_pid = process.pid
    # Reading proc.json here verifies that publication preceded the CLI launch.
    write_json(directory / "ready.json", {
        "pid": os.getpid(), "child_pid": child_pid, "cwd": str(Path.cwd()),
        "stdin": sys.stdin.buffer.read().decode("utf-8"),
        "env": {key: os.environ.get(key) for key in scenario.get("capture_env", [])},
        "proc": json.loads((directory / "proc.json").read_text(encoding="utf-8")),
    })
    events = scenario.get("events", [])
    if isinstance(events, str):
        sys.stdout.buffer.write(Path(events).read_bytes())
    else:
        for event in events:
            sys.stdout.buffer.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(scenario.get("stderr", "").encode("utf-8"))
    sys.stderr.buffer.flush()
    if scenario.get("hang"):
        while True:
            time.sleep(1)
    time.sleep(scenario.get("sleep", 0))
    if "report" in scenario:
        Path(scenario["report_path"]).write_text(scenario["report"], encoding="utf-8")
    return scenario.get("exit", 0)


if __name__ == "__main__":
    raise SystemExit(main())
