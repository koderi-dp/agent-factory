"""Two throwaway v2 repos using the engine from this checkout.

uv run --locked python tools/sandbox.py new
uv run --locked python tools/sandbox.py run          # fake CLI, all six Full jobs
uv run --locked python tools/sandbox.py run --real   # Codex Luna/Light, fresh suite
uv run --locked python tools/sandbox.py run --real --resume  # explicitly resume paused jobs
uv run --locked python tools/sandbox.py status

--dir is the suite directory; machine/ is its isolated FACTORY_HOME. Evidence is
retained there. Claude/mixed-provider parity remains deferred to session 1.11.
The bench command still reads a legacy v1 repo; v2 benchmarking is session 2.3.
"""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Any

from agent_factory import run_cli
from agent_factory.engine import gitops, scaffold
from agent_factory.engine.adapters.codex import CodexAdapter
from agent_factory.engine.adapters.base import Prepared
from agent_factory.engine.config import load_machine_settings, save_machine_settings
from agent_factory.engine.runtime import Runtime
from agent_factory.engine.scheduler import Claim

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = HERE / "sandbox-fixture"
DEFAULT_DIR = Path(tempfile.gettempdir()) / "factory-sandbox-v2"
BACKLOG = [
    'Change the Add button\'s text to "Add todo"',
    'Show the number of open todos in the page title, like "Todos (3)"',
    'Ignore a new todo whose title is already in the list',
]


@contextmanager
def isolated_home(suite: Path) -> Iterator[None]:
    previous = os.environ.get("FACTORY_HOME")
    os.environ["FACTORY_HOME"] = str(suite / "machine")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FACTORY_HOME", None)
        else:
            os.environ["FACTORY_HOME"] = previous


def repos(suite: Path) -> list[Path]:
    return [suite / "todo-app-1", suite / "todo-app-2"]


def new(suite: Path) -> None:
    if suite.exists():
        raise ValueError(f"{suite} already exists; use a fresh --dir or clean first")
    suite.mkdir(parents=True)
    with isolated_home(suite):
        for root in repos(suite):
            shutil.copytree(FIXTURE, root)
            (root / "AGENTS.md").write_bytes((root / "CLAUDE.md").read_bytes())
            gitops.checked("init", "-q", "-b", "main", cwd=root)
            gitops.checked("config", "user.name", "factory-sandbox", cwd=root)
            gitops.checked("config", "user.email", "sandbox@localhost", cwd=root)
            gitops.checked("config", "core.autocrlf", "false", cwd=root)
            gitops.checked("add", "-A", cwd=root)
            gitops.checked("commit", "-q", "-m", "Start the todo app", cwd=root)
            scaffold.init(root, "codex")
            path = root / "factory/config.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config.update(defaultPipeline="full", merge="auto", maxRounds=1, permissions="bypass")
            for steps in config["pipelines"].values():
                for step in steps:
                    for entry in step.get("parallel", [step]):
                        entry["profiles"]["codex"] = {"model": "gpt-6-luna", "effort": "low"}
            run_cli.atomic_json(path, config)
            (root / "factory/backlog.md").write_text("# Backlog\n\n" + "".join(f"- [ ] {item}\n"
                                                                               for item in BACKLOG),
                                                     encoding="utf-8")
            gitops.checked("add", "-A", cwd=root)
            gitops.checked("commit", "-q", "-m", "Configure the v2 sandbox", cwd=root)
    print(f"Sandbox ready: {suite} (two repos, Full, Codex Luna/Light)", flush=True)


class SandboxAdapter(CodexAdapter):
    """Use production packet parsing/classification with a deterministic fake process."""

    def prepare(self, context: Claim) -> Prepared:
        prepared = super().prepare(context)
        scenario = {
            "sleep":
            0.2,
            "sandbox": {
                "title": context.job["title"],
                "uses": context.execution["uses"],
                "ports": list(context.ports)
            },
            "report_path":
            str(context.directory / "report.md"),
            "invocation_marker":
            str(context.directory / "invocations"),
            "events": [{
                "type": "thread.started",
                "thread_id": f"fake-{context.attempt['owner_token']}"
            }, {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 2
                }
            }],
        }
        run_cli.atomic_json(context.directory / "scenario.json", scenario)
        command = {
            "argv": [
                sys._base_executable, "-I", "-S",
                str(ROOT / "tests/fake_cli.py"), "--scenario",
                str(context.directory / "scenario.json")
            ],
            "cwd":
            context.job["worktree"],
            "env": {},
            "stdin":
            "prompt.md"
        }
        return Prepared(command, prepared.prompt, prepared.system, prepared.packet,
                        prepared.packet_bytes)


class SandboxCodexAdapter(CodexAdapter):
    """Controlled runs retain auth/project instructions, without user config/MCPs."""

    def command(self, context: Claim) -> dict[str, Any]:
        command = super().command(context)
        command["argv"].insert(2, "--ignore-user-config")
        return command


def _job_states(runtime: Runtime) -> dict[str, Any]:
    return {
        identifier: runtime.snapshot(identifier)["jobs"]
        for identifier in runtime.scheduler.repos
    }


def _peak_concurrency(attempts: list[dict[str, Any]]) -> tuple[int, int]:
    """Include earlier process intervals when continuing an interrupted suite."""
    events = []
    for attempt in attempts:
        start, end = _timestamp(attempt["t_launch"]), _timestamp(attempt["t_exit"])
        if start is not None and end is not None and end > start:
            events.extend(((start, 1, attempt["repo_id"]), (end, -1, attempt["repo_id"])))
    active: dict[str, int] = {}
    peak_live, peak_repos = 0, 0
    for _, delta, repo_id in sorted(events):
        active[repo_id] = active.get(repo_id, 0) + delta
        peak_live = max(peak_live, sum(active.values()))
        peak_repos = max(peak_repos, sum(count > 0 for count in active.values()))
    return peak_live, peak_repos


def verify_main(root: Path) -> None:
    subprocess.run(["node", "--test"], cwd=root, check=True, capture_output=True)
    if 'Add todo</button>' not in (root / "index.html").read_text(encoding="utf-8"):
        raise AssertionError(f"{root}: button feature missing on main")
    app = (root / "src/app.js").read_text(encoding="utf-8")
    if "document.title" not in app:
        raise AssertionError(f"{root}: page title feature missing on main")
    # Exercise the public pure function independently of any agent-written test.
    script = """import { addTodo } from './src/todos.js';
const a = addTodo([], 'same');
if (addTodo(a, 'same').length !== 1) process.exit(1);
if (addTodo(a, 'different').length !== 2) process.exit(2);
const node = () => ({ children: [], append(...items) { this.children.push(...items) },
  addEventListener(event, handler) { this[event] = handler },
  set innerHTML(value) { this.children = [] } });
const elements = new Map();
globalThis.document = { title: 'Todos', createElement: node,
  getElementById(id) { if (!elements.has(id)) elements.set(id, node()); return elements.get(id) } };
globalThis.localStorage = { getItem: () => JSON.stringify([{ id: '1', title: 'existing', done: false }]),
  setItem() {} };
await import('./src/app.js');
if (document.title !== 'Todos (1)') process.exit(3);
elements.get('list').children[0].children[0].change();
if (!['Todos', 'Todos (0)'].includes(document.title)) process.exit(4);
const input = { value: 'next', focus() {} };
elements.get('new-todo').submit({ preventDefault() {}, target: { elements: { title: input } } });
if (document.title !== 'Todos (1)') process.exit(5);
"""
    subprocess.run(["node", "--input-type=module", "-e", script],
                   cwd=root,
                   check=True,
                   capture_output=True)
    if gitops.checked("status", "--porcelain", "--", ".", ":(exclude)factory/backlog.md", cwd=root):
        raise AssertionError(f"{root}: main is dirty")


def run(suite: Path,
        *,
        real: bool = False,
        timeout: float = 3600,
        port_range: str | None = None,
        resume: bool = False) -> dict:
    if not all((root / "factory/config.json").exists() for root in repos(suite)):
        raise ValueError("no v2 sandbox; run new first")
    with isolated_home(suite):
        runtime = Runtime() if real else Runtime({"codex": SandboxAdapter()})
        if real:
            settings = runtime.scheduler.machine.call(load_machine_settings)
            runtime.supervisor.adapters["codex"] = SandboxCodexAdapter(settings)
        started = time.monotonic()
        peak_live, peak_repos = 0, 0
        lease_history = set()
        owns_jobs = False
        try:
            if port_range is not None:
                runtime.scheduler.machine.call(
                    lambda c: save_machine_settings(c, {"ports": {
                        "range": port_range
                    }}))
            runtime.start()
            existing = _job_states(runtime)
            if any(existing.values()) and not resume:
                raise ValueError(
                    "this suite already has jobs; use a fresh --dir to retain evidence")
            owns_jobs = True
            if resume:
                if len(existing) != 2 or any(
                        len(jobs) != len(BACKLOG) for jobs in existing.values()):
                    raise ValueError("resume requires the existing six-job suite")
                for identifier, jobs in existing.items():
                    for job in jobs:
                        if job["state"] == "paused":
                            runtime.action(identifier, job["id"], "resume", {})
            else:
                # Queue every feature in both repos before the first scheduling tick.
                for identifier in runtime.scheduler.repos:
                    for _ in BACKLOG:
                        runtime.edit_backlog(identifier, {"action": "start", "pipeline": "full"})
            previous = None
            while time.monotonic() - started < timeout:
                runtime.tick()
                states = _job_states(runtime)
                leases = runtime.scheduler.machine.call(
                    lambda c: [dict(r) for r in c.execute("SELECT * FROM port_leases")])
                bases = [r["base"] for r in leases]
                if len(bases) != len(set(bases)):
                    raise AssertionError("colliding machine port leases")
                lease_history.update((r["repo_id"], r["job_id"], r["base"]) for r in leases)
                live = [
                    writer.call(lambda c: c.execute(
                        "SELECT count(*) FROM attempts WHERE state IN ('launching','running')").
                                fetchone()[0]) for _, writer in runtime.scheduler.repos.values()
                ]
                peak_live = max(peak_live, sum(live))
                peak_repos = max(peak_repos, sum(n > 0 for n in live))
                summary = [(repo_id, j["id"], j["state"], j["current_step"], j["note"])
                           for repo_id, jobs in states.items() for j in jobs]
                if summary != previous:
                    print(json.dumps(summary), flush=True)
                    previous = summary
                failed = [row for row in summary if row[2] in {"paused", "needs_human"}]
                if failed:
                    raise AssertionError(f"jobs need intervention: {failed}")
                if all(j["state"] == "merged" for jobs in states.values() for j in jobs):
                    break
                time.sleep(0.1 if not real else 0.5)
            else:
                raise TimeoutError("sandbox jobs did not finish before timeout")
            for root in repos(suite):
                verify_main(root)
            runtime.supervisor.sync_ports()
            if runtime.scheduler.machine.call(
                    lambda c: c.execute("SELECT count(*) FROM port_leases").fetchone()[0]):
                raise AssertionError("merged jobs retained port leases")
            attempts = []
            for repo_id, jobs in states.items():
                for job in jobs:
                    attempts.extend({
                        "repo_id": repo_id,
                        "job_id": job["id"],
                        **attempt
                    } for attempt in runtime.snapshot(repo_id, job["id"])["attempts"])
            historical_live, historical_repos = _peak_concurrency(attempts)
            peak_live, peak_repos = max(peak_live,
                                        historical_live), max(peak_repos, historical_repos)
            if peak_repos != 2:
                raise AssertionError("both repos never ran concurrently")
            result = {
                "provider": "codex" if real else "fake",
                "model": "gpt-6-luna",
                "effort": "low",
                "jobs": 6,
                "attempts": len(attempts),
                "wall_seconds": round(time.monotonic() - started, 2),
                "peak_live": peak_live,
                "peak_repos": peak_repos,
                "leases": [list(row) for row in sorted(lease_history)],
                "all_merged": True,
                "resumed": resume,
                "ignore_user_config": real,
                "claude_parity": "pending session 1.11"
            }
            run_cli.atomic_json(suite / "result.json", result)
            print(json.dumps(result, indent=2), flush=True)
            return result
        finally:
            # A failed run must not leave paid calls/servers running. Preserve all files.
            if owns_jobs:
                for repo_id, jobs in _job_states(runtime).items():
                    for job in jobs:
                        if job["state"] in {"running", "paused"}:
                            runtime.supervisor.stop(repo_id, job["id"])
                deadline = time.monotonic() + 20
                while runtime.scheduler.recount() and time.monotonic() < deadline:
                    runtime.supervisor.poll()
                    time.sleep(0.1)
            runtime.close()


def status(suite: Path) -> None:
    with isolated_home(suite):
        runtime = Runtime()
        try:
            print(json.dumps(_job_states(runtime), indent=2))
        finally:
            runtime.close()


def clean(suite: Path) -> None:
    # Only suites we created, and never remove evidence of an active wrapper.
    resolved = suite.resolve()
    if resolved == ROOT or resolved in ROOT.parents or ROOT in resolved.parents or not (
            resolved / "machine/machine.db").is_file():
        raise ValueError(f"not a sandbox suite: {resolved}")
    with isolated_home(resolved):
        runtime = Runtime()
        try:
            runtime.start()
            if runtime.scheduler.recount():
                raise ValueError("sandbox has active attempts; stop them before cleaning")
        finally:
            runtime.close()

    def unlock(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(resolved, onexc=unlock) if sys.version_info >= (3, 12) else shutil.rmtree(
        resolved, onerror=unlock)


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

    tokens = total(
        ("input_tokens", "output_tokens", "cache_read_input_tokens", "cached_input_tokens"))
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
        points = [(h.get("stage", "unknown"), _timestamp(h.get("at"))) for h in history
                  if isinstance(h, dict)]
        stage_times = {}
        terminal_stages = {"approved", "merged", "needs-human"}
        for (stage, start), (_, end) in zip(points, points[1:]):
            if (stage not in terminal_stages and start is not None and end is not None
                    and end >= start):
                stage_times[stage] = stage_times.get(stage, 0) + end - start
        created = _timestamp(job.get("created")) or (points[0][1] if points else None)
        terminal = bool(history
                        and history[-1].get("stage") in {"approved", "merged", "needs-human"})
        ended = (_timestamp(job.get("updated")) if terminal else None) or (points[-1][1]
                                                                           if points else None)
        if (terminal and points and points[-1][0] not in terminal_stages and ended is not None
                and points[-1][1] is not None):
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
        rows.append({
            "id": job.get("id", path.parent.name),
            "feature": job.get("feature", ""),
            "wall": wall,
            "stages": stage_times,
            "agents": sum(len(run.get("reports", {})) for run in runs),
            "tokens": tokens,
            "cost": cost
        })
    return rows


def _duration(seconds):
    return f"{seconds:.1f}s" if seconds is not None else "unknown"


def _benchmark_markdown(rows):
    lines = [
        "| Job | Feature | Wall time | Stage times | Agent runs | Tokens | Cost (USD) |",
        "|---|---|---:|---|---:|---:|---:|"
    ]
    for row in rows:
        stages = ", ".join(f"{name}: {_duration(seconds)}"
                           for name, seconds in row["stages"].items()) or "unknown"
        tokens = f"{row['tokens']:.0f}" if row["tokens"] is not None else "unknown"
        cost = f"${row['cost']:.4f}" if row["cost"] is not None else "unknown"
        feature = str(row["feature"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['id']} | {feature} | {_duration(row['wall'])} | {stages} | "
                     f"{row['agents']} | {tokens} | {cost} |")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["new", "run", "status", "clean", "bench"])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--real",
                        action="store_true",
                        help="run Codex Luna/Light instead of the fake CLI")
    parser.add_argument("--timeout",
                        type=float,
                        default=3600,
                        help="whole-suite deadline in seconds")
    parser.add_argument("--ports",
                        help="machine port range; use distinct ranges for independent suites")
    parser.add_argument("--resume",
                        action="store_true",
                        help="continue an existing suite, resuming paused jobs")
    args = parser.parse_args()
    suite = Path(os.path.expandvars(str(args.dir))).expanduser().resolve()
    if suite == ROOT or ROOT in suite.parents or suite == Path(suite.anchor):
        parser.error("the sandbox must be outside this repo and not a filesystem root")
    if args.command == "run":
        run(suite, real=args.real, timeout=args.timeout, port_range=args.ports, resume=args.resume)
    else:
        {"new": new, "status": status, "clean": clean, "bench": bench}[args.command](suite)


if __name__ == "__main__":
    main()
