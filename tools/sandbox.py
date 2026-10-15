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
import re
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = HERE / "sandbox-fixture"
DEFAULT_DIR = Path(tempfile.gettempdir()) / "factory-sandbox" / "todo-app"

# Light settings: the cheapest models, little thinking, one review round, merge straight away.
LIGHT_CONFIG = json.loads((ROOT / "factory/config.json").read_text(encoding="utf-8"))
LIGHT_CONFIG.update(merge="auto", maxRounds=1)
for step in LIGHT_CONFIG["pipeline"]:
    for execution in step.get("parallel", [step]):
        execution["providers"] = {
            "claude": {"model": "haiku", "effort": "low"},
            "codex": {"model": "gpt-6-luna", "effort": "low"},
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
    for skill in (ROOT / "claude" / "skills").glob("factory*"):
        shutil.copytree(skill, sandbox / ".claude" / "skills" / skill.name)
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


def _timestamp(value):
    """Parse the UTC timestamps stored in v1 job history."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _log_usage(path):
    """Return token and cost totals found in a headless log, or None when absent."""
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    # Claude's structured result and Codex usage events both use these field names.
    def total(names):
        values = []
        for name in names:
            values.extend(re.findall(r'"' + re.escape(name) + r'"\s*:\s*(\d+(?:\.\d+)?)', text))
        return sum(float(v) for v in values) if values else None

    tokens = total(("input_tokens", "output_tokens", "cache_read_input_tokens", "cached_input_tokens"))
    cost = total(("total_cost_usd", "cost_usd"))
    return tokens, cost


def benchmark_rows(sandbox):
    """Read v1 jobs and their corresponding headless logs into printable rows."""
    rows = []
    jobs_dir = sandbox / "factory" / "jobs"
    for path in sorted(jobs_dir.glob("*/job.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        history = job.get("history", [])
        points = [(h.get("stage", "unknown"), _timestamp(h.get("at")))
                  for h in history if isinstance(h, dict)]
        stage_times = {}
        terminal_stages = {"approved", "merged", "needs-human"}
        for (stage, start), (_, end) in zip(points, points[1:]):
            if (stage not in terminal_stages and start is not None and end is not None
                    and end >= start):
                stage_times[stage] = stage_times.get(stage, 0) + end - start
        created = _timestamp(job.get("created")) or (points[0][1] if points else None)
        terminal = bool(history and history[-1].get("stage") in {"approved", "merged", "needs-human"})
        ended = (_timestamp(job.get("updated")) if terminal else None) or (points[-1][1] if points else None)
        if (terminal and points and points[-1][0] not in terminal_stages
                and ended is not None and points[-1][1] is not None):
            final_stage = points[-1][0]
            if ended >= points[-1][1]:
                stage_times[final_stage] = stage_times.get(final_stage, 0) + ended - points[-1][1]
        wall = ended - created if created is not None and ended is not None and ended >= created else None
        # A headless Claude invocation logs directly in jobs/. Codex session metadata
        # carries the same basename as its log.
        logs = sorted(jobs_dir.glob("headless-*.log"))
        tokens = cost = None
        for log in logs:
            try:
                log_text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if f"SANDBOX-FEATURE: {job.get('feature', '')}" in log_text:
                tokens, cost = _log_usage(log)
                break
        else:
            # Historical headless sessions have no feature marker. A lone log is
            # unambiguous; with several logs, report usage as unknown per job.
            if len(logs) == 1:
                tokens, cost = _log_usage(logs[0])
        runs = job.get("runs", [])
        rows.append({"id": job.get("id", path.parent.name), "feature": job.get("feature", ""),
                     "wall": wall, "stages": stage_times,
                     "agents": sum(len(run.get("reports", {})) for run in runs),
                     "tokens": tokens, "cost": cost})
    return rows


def _duration(seconds):
    return f"{seconds:.1f}s" if seconds is not None else "unknown"


def _benchmark_markdown(rows):
    lines = ["| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |",
             "|---|---|---:|---|---:|---:|---:|"]
    for row in rows:
        stages = ", ".join(
            f"{name}: {_duration(seconds)}" for name, seconds in row["stages"].items()
        ) or "unknown"
        tokens = f"{row['tokens']:.0f}" if row["tokens"] is not None else "unknown"
        cost = f"${row['cost']:.4f}" if row["cost"] is not None else "unknown"
        feature = str(row["feature"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['id']} | {feature} | {_duration(row['wall'])} | {stages} | "
            f"{row['agents']} | {tokens} | {cost} |"
        )
    if not rows:
        lines.append("| No jobs found | | unknown | unknown | 0 | unknown | unknown |")
    return "\n".join(lines)


def bench(sandbox):
    if not sandbox.exists():
        sys.exit("no sandbox yet; run: python tools/sandbox.py new")
    rows = benchmark_rows(sandbox)
    table = _benchmark_markdown(rows)
    print(table)
    target = ROOT / "docs" / "benchmarks.md"
    heading = "## v1 baseline"
    capture_heading = "### Latest capture"
    existing = target.read_text(encoding="utf-8") if target.exists() else "# Benchmarks\n"
    capture = capture_heading + "\n\n" + table + "\n"
    if capture_heading in existing:
        before, rest = existing.split(capture_heading, 1)
        next_section = re.search(r"\n#{2,3} ", rest)
        existing = before + capture + (rest[next_section.start():] if next_section else "")
    elif heading in existing:
        prefix, rest = existing.split(heading, 1)
        next_section = re.search(r"\n## ", rest)
        after_heading = rest[next_section.start():] if next_section else ""
        body = rest[:next_section.start()] if next_section else rest
        existing = prefix + heading + body.rstrip() + "\n\n" + capture + after_heading
    else:
        existing = existing.rstrip() + "\n\n" + heading + "\n\n" + capture
    target.write_text(existing, encoding="utf-8")
    print(f"\nWrote {target}")


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
        items = queued(sandbox)
        if not items:
            break
        feature = items[0]
        log_dir = sandbox / "factory" / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"headless-{time.time_ns()}.log"
        print(f"\n=== job {n} of {count}: {feature} (log: {log.name})", flush=True)
        with log.open("w", encoding="utf-8") as out:
            out.write(f"SANDBOX-FEATURE: {feature}\n")
            out.flush()
            subprocess.run([claude, "-p", "/factory next", "--permission-mode", "bypassPermissions"],
                           cwd=sandbox, stdout=out, stderr=subprocess.STDOUT)
    print(f"\nfinished in {int(time.time() - started)} s\n")
    status(sandbox)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["new", "serve", "run", "status", "reset", "clean", "bench"])
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
    elif args.command == "bench":
        bench(sandbox)
    elif args.command == "run":
        run(sandbox, args.jobs, args.skip_permissions)


if __name__ == "__main__":
    main()
