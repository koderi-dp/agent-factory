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


def server(path: Path, ignore: bool, port: int = 0) -> None:
    ignore_term(ignore)
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
        write_json(path, {
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "port": listener.getsockname()[1]
        })
        while True:
            connection, _ = listener.accept()
            connection.close()


def child(path: Path,
          ignore: bool,
          port: int = 0,
          orphan: bool = False,
          escape: bool = False) -> None:
    ignore_term(ignore)
    subprocess.Popen([
        sys._base_executable, "-I", "-S",
        str(Path(__file__).resolve()), "--server",
        str(path), "--ignore-term",
        str(int(ignore)), "--port",
        str(port)
    ],
                     stdin=subprocess.DEVNULL,
                     close_fds=True,
                     start_new_session=escape and os.name != "nt")
    if orphan:
        return
    while True:
        time.sleep(1)


def sandbox_execution(scenario: dict) -> str:
    """Full-pipeline fixture: real commits and node checks in an isolated worktree."""
    execution = scenario["sandbox"]
    title, role = execution["title"], execution["uses"]
    if role == "spec-writer":
        return f"# {title}\n\n## Acceptance criteria\nThe requested feature works.\n"
    if role == "builder":
        if "Add button" in title:
            path = Path("index.html")
            path.write_text(path.read_text(encoding="utf-8").replace('>Add</button>',
                                                                     '>Add todo</button>'),
                            encoding="utf-8")
        elif "page title" in title:
            path = Path("src/app.js")
            path.write_text(path.read_text(encoding="utf-8").replace(
                "function render() {",
                "function render() {\n  document.title = `Todos (${remaining(todos)})`"),
                            encoding="utf-8")
        else:
            path = Path("src/todos.js")
            path.write_text(path.read_text(encoding="utf-8").replace(
                "if (!text) return todos",
                "if (!text || todos.some(t => t.title === text)) return todos"),
                            encoding="utf-8")
        subprocess.run(["node", "--test"], check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", title], check=True, capture_output=True)
        return "STATUS: READY\n\n## Checks run\nnode --test: PASS\n"
    subprocess.run(["node", "--test"], check=True, capture_output=True)
    return ("APPROVE" if role in {"approver", "fast-approver"} else
            "VERDICT: PASS") + "\n\n## Checks run\nnode --test: PASS\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, default=os.environ.get("FACTORY_FAKE_SCENARIO"))
    parser.add_argument("--child", type=Path)
    parser.add_argument("--server", type=Path)
    parser.add_argument("--ignore-term", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--orphan", action="store_true")
    parser.add_argument("--escape", action="store_true")
    args = parser.parse_args()
    if args.server:
        server(args.server, bool(args.ignore_term), args.port)
    if args.child:
        child(args.child, bool(args.ignore_term), args.port, args.orphan, args.escape)
        return 0
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    ignore_term(scenario.get("ignore_sigterm", False))
    directory = args.scenario.parent
    markers = Path(scenario.get("invocation_marker", directory / "invocations"))
    markers.mkdir(parents=True, exist_ok=True)
    write_json(markers / f"{uuid.uuid4().hex}.json", {"pid": os.getpid()})
    # Hold assigned ports throughout each fake execution. Bind failures are real
    # failures, so overlapping assignments cannot masquerade as a passing run.
    listeners = []
    if "sandbox" in scenario or "bind_ports" in scenario:
        for port in scenario.get("bind_ports", scenario.get("sandbox", {}).get("ports", [])):
            listener = socket.socket()
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen()
            listeners.append(listener)
        write_json(directory / "bound-ports.json",
                   scenario.get("bind_ports",
                                scenario.get("sandbox", {}).get("ports", [])))
        if "sandbox" in scenario:
            scenario["report"] = sandbox_execution(scenario)
    child_pid = None
    if scenario.get("spawn_child_listening_on_port"):
        process = subprocess.Popen([
            sys._base_executable, "-I", "-S",
            str(Path(__file__).resolve()), "--child",
            str(directory / "server.json"), "--ignore-term",
            str(int(scenario.get("ignore_sigterm", False))), "--port",
            str(scenario.get("server_port",
                             0)), *(["--orphan"] if scenario.get("orphan_server") else []),
            *(["--escape"] if scenario.get("server_new_session") else [])
        ],
                                   stdin=subprocess.DEVNULL,
                                   close_fds=True)
        child_pid = process.pid
    # Reading proc.json here verifies that publication preceded the CLI launch.
    write_json(
        directory / "ready.json", {
            "pid": os.getpid(),
            "child_pid": child_pid,
            "cwd": str(Path.cwd()),
            "stdin": sys.stdin.buffer.read().decode("utf-8"),
            "env": {
                key: os.environ.get(key)
                for key in scenario.get("capture_env", [])
            },
            "proc": json.loads((directory / "proc.json").read_text(encoding="utf-8")),
        })
    events = scenario.get("events", [])
    if isinstance(events, str):
        sys.stdout.buffer.write(Path(events).read_bytes())
    else:
        for event in events:
            time.sleep(scenario.get("event_delay", 0))
            sys.stdout.buffer.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
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
