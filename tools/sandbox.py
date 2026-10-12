"""A throwaway repo for trying the whole factory quickly, with light settings and a fixed backlog.

  python tools/sandbox.py new        build it: the small todo app in tools/sandbox-fixture/, this
                                     repo's agents, skill and factory/, light settings, 3 tiny features
  python tools/sandbox.py serve      start its dashboard (default http://127.0.0.1:5097/)
  python tools/sandbox.py run        run the backlog headless; needs --skip-permissions (see below)
  python tools/sandbox.py status     show its jobs, backlog and branches
  python tools/sandbox.py reset      delete it (worktrees, branches, folders) and build it again
  python tools/sandbox.py clean      delete it

The sandbox lives in <temp>/factory-sandbox/todo-app unless --dir says otherwise, with job
worktrees beside it in todo-app-factory/. Nothing outside those two folders is touched.

`run` starts `claude -p "/factory next"` in the sandbox once per backlog item. A headless session
can't answer permission prompts, so it only runs with --skip-permissions, which passes
`--permission-mode bypassPermissions`: the agents then run their commands without asking. Without
the flag, `run` prints the steps to do the same in an interactive Claude Code session instead.
"""

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = HERE / "sandbox-fixture"
DEFAULT_DIR = Path(tempfile.gettempdir()) / "factory-sandbox" / "todo-app"

# Light settings: the cheapest models, little thinking, one review round, merge straight away.
LIGHT_CONFIG = {
    "models": {a: "haiku" for a in (
        "spec-writer", "builder", "reviewer-security", "reviewer-ux", "reviewer-ui", "reviewer-code", "approver")},
    "efforts": {a: "low" for a in (
        "spec-writer", "builder", "reviewer-security", "reviewer-ux", "reviewer-ui", "reviewer-code", "approver")},
    "merge": "auto",
    "maxRounds": 1,
}

# Small, independent changes that each touch one or two files and are quick to check.
BACKLOG = [
    "Change the Add button's text to \"Add todo\"",
    "Show the number of open todos in the page title, like \"Todos (3)\"",
    "Ignore a new todo whose title is already in the list",
]


def git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def worktrees_dir(sandbox):
    return sandbox.parent / f"{sandbox.name}-factory"


def force_remove(path):
    """Delete a folder, including git's read-only object files on Windows."""
    def unlock(func, p, _):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if path.exists():
        shutil.rmtree(path, onexc=unlock) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=unlock)


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def new(sandbox):
    if sandbox.exists():
        sys.exit(f"{sandbox} already exists; use reset to rebuild it")
    sandbox.mkdir(parents=True)
    shutil.copytree(FIXTURE, sandbox, dirs_exist_ok=True)
    # The factory exactly as it is in this repo now.
    shutil.copytree(ROOT / "claude" / "agents", sandbox / ".claude" / "agents")
    shutil.copytree(ROOT / "claude" / "skills" / "factory", sandbox / ".claude" / "skills" / "factory")
    shutil.copytree(ROOT / "factory", sandbox / "factory",
                    ignore=shutil.ignore_patterns("jobs", "__pycache__", "*.tmp", ".lock"))
    (sandbox / "factory" / "config.json").write_text(json.dumps(LIGHT_CONFIG, indent=2) + "\n", encoding="utf-8")
    (sandbox / "factory" / "backlog.md").write_text(
        "# Backlog\n\n`/factory next` builds the first unchecked item and checks it off with its job id.\n\n"
        + "".join(f"- [ ] {item}\n" for item in BACKLOG), encoding="utf-8")
    (sandbox / ".gitignore").write_text("/factory/jobs/\n", encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=sandbox)
    git("config", "user.name", "factory-sandbox", cwd=sandbox)
    git("config", "user.email", "sandbox@localhost", cwd=sandbox)
    git("config", "core.autocrlf", "false", cwd=sandbox)
    git("add", "-A", cwd=sandbox)
    git("commit", "-q", "-m", "Start the todo app", cwd=sandbox)
    print(f"sandbox ready: {sandbox}")
    print(f"  {len(BACKLOG)} features queued, light settings (haiku, low effort, 1 round, auto merge)")
    print("  next: python tools/sandbox.py serve   (dashboard)")
    print("        python tools/sandbox.py run     (run the backlog)")


def clean(sandbox):
    # Stop servers the agents left running first: on Windows they keep the worktrees from being deleted.
    for job_json in (sandbox / "factory" / "jobs").glob("*/job.json"):
        subprocess.run([sys.executable, "factory/job.py", "free-ports", job_json.parent.name],
                       cwd=sandbox, capture_output=True)
    if (sandbox / ".git").exists():
        for line in git("worktree", "list", "--porcelain", cwd=sandbox).stdout.splitlines():
            if line.startswith("worktree ") and Path(line[9:]).resolve() != sandbox.resolve():
                git("worktree", "remove", "--force", line[9:], cwd=sandbox, check=False)
    force_remove(worktrees_dir(sandbox))
    force_remove(sandbox)
    print(f"removed {sandbox} and {worktrees_dir(sandbox)}")


def serve(sandbox, port):
    if not sandbox.exists():
        sys.exit("no sandbox yet; run: python tools/sandbox.py new")
    if port_in_use(port):
        sys.exit(f"port {port} is busy; is the dashboard already running? (--port picks another)")
    print(f"dashboard: http://127.0.0.1:{port}/   (Ctrl+C stops it)", flush=True)
    subprocess.run([sys.executable, "factory/server.py", "--port", str(port)], cwd=sandbox)


def queued(sandbox):
    path = sandbox / "factory" / "backlog.md"
    return [l[6:] for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("- [ ] ")] if path.exists() else []


def status(sandbox):
    if not sandbox.exists():
        sys.exit("no sandbox yet; run: python tools/sandbox.py new")
    jobs = sorted((sandbox / "factory" / "jobs").glob("*/job.json"))
    print(f"sandbox: {sandbox}")
    for j in jobs:
        data = json.loads(j.read_text(encoding="utf-8"))
        note = f"  ({data['note']})" if data.get("note") else ""
        print(f"  {data['id']:<36} {data['stage']:<12} round {data.get('round', 0)}{note}")
    if not jobs:
        print("  no jobs yet")
    print(f"backlog: {len(queued(sandbox))} queued")
    for item in queued(sandbox):
        print(f"  - {item}")
    print("main:", git("log", "--oneline", "-1", cwd=sandbox).stdout.strip())
    branches = git("branch", "--list", "factory/*", cwd=sandbox).stdout.split()
    print("open job branches:", ", ".join(b for b in branches if b not in "*+") or "none")
    r = subprocess.run(["node", "--test"], cwd=sandbox, capture_output=True, text=True)
    print("node --test on main:", "pass" if r.returncode == 0 else "FAIL")


def run(sandbox, jobs, skip_permissions):
    if not sandbox.exists():
        sys.exit("no sandbox yet; run: python tools/sandbox.py new")
    if not skip_permissions:
        print("To run the backlog with Claude Code asking before each command:")
        print(f"  1. cd {sandbox}")
        print("  2. claude")
        print("  3. type /factory next, once per queued feature")
        print("Or run it headless, with no permission prompts, inside this sandbox only:")
        print("  python tools/sandbox.py run --skip-permissions")
        return
    claude = shutil.which("claude")
    if not claude:
        sys.exit("the claude command isn't on PATH")
    count = jobs or len(queued(sandbox))
    started = time.time()
    for n in range(1, count + 1):
        if not queued(sandbox):
            break
        print(f"\n=== job {n} of {count}: {queued(sandbox)[0]}", flush=True)
        subprocess.run([claude, "-p", "/factory next", "--permission-mode", "bypassPermissions"], cwd=sandbox)
    print(f"\nfinished in {int(time.time() - started)} s\n")
    status(sandbox)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["new", "serve", "run", "status", "reset", "clean"])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help=f"sandbox folder (default {DEFAULT_DIR})")
    parser.add_argument("--port", type=int, default=5097, help="dashboard port for serve")
    parser.add_argument("--jobs", type=int, default=0, help="run at most this many jobs (default: the whole backlog)")
    parser.add_argument("--skip-permissions", action="store_true",
                        help="let run start headless sessions that don't ask before running commands")
    args = parser.parse_args()
    # Expand %TEMP%, $TEMP and ~ here, since not every shell does it for us.
    sandbox = Path(os.path.expandvars(os.path.expanduser(str(args.dir)))).resolve()
    if sandbox == ROOT or ROOT in sandbox.parents:
        sys.exit("the sandbox must be outside this repo")
    if args.command == "new":
        new(sandbox)
    elif args.command == "reset":
        clean(sandbox)
        new(sandbox)
    elif args.command == "clean":
        clean(sandbox)
    elif args.command == "serve":
        serve(sandbox, args.port)
    elif args.command == "status":
        status(sandbox)
    elif args.command == "run":
        run(sandbox, args.jobs, args.skip_permissions)


if __name__ == "__main__":
    main()
