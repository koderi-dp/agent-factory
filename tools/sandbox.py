"""Two throwaway v2 repos using the engine from this checkout.

uv run --locked python tools/sandbox.py new
uv run --locked python tools/sandbox.py run          # fake CLI, all six Full jobs
uv run --locked python tools/sandbox.py run --real   # Codex Luna/Light, fresh suite
uv run --locked python tools/sandbox.py run --real --resume  # explicitly resume paused jobs
uv run --locked python tools/sandbox.py status

--dir is the suite directory; machine/ is its isolated FACTORY_HOME. Evidence is
retained there. Claude/mixed-provider parity remains deferred to session 1.11.
Use run --pipeline fast for Fast; new --model/--effort choose Codex profiles.
bench --compare OTHER_SUITE compares Fast and Full by feature and repo.
"""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
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


def new(suite: Path, *, model: str = "gpt-6-luna", effort: str = "low") -> None:
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
                        entry["profiles"]["codex"] = {"model": model, "effort": effort}
            run_cli.atomic_json(path, config)
            (root / "factory/backlog.md").write_text("# Backlog\n\n" + "".join(f"- [ ] {item}\n"
                                                                               for item in BACKLOG),
                                                     encoding="utf-8")
            gitops.checked("add", "-A", cwd=root)
            gitops.checked("commit", "-q", "-m", "Configure the v2 sandbox", cwd=root)
    print(f"Sandbox ready: {suite} (two repos, Codex {model}/{effort})", flush=True)


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
        resume: bool = False,
        pipeline: str = "full",
        keep_going: bool = False) -> dict:
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
                        runtime.edit_backlog(identifier, {"action": "start", "pipeline": pipeline})
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
                if failed and not keep_going:
                    raise AssertionError(f"jobs need intervention: {failed}")
                finished = {"merged", "paused", "needs_human"} if keep_going else {"merged"}
                if all(j["state"] in finished for jobs in states.values()
                       for j in jobs) and not runtime.scheduler.recount():
                    break
                time.sleep(0.1 if not real else 0.5)
            else:
                raise TimeoutError("sandbox jobs did not finish before timeout")
            all_merged = all(j["state"] == "merged" for jobs in states.values() for j in jobs)
            if all_merged:
                for root in repos(suite):
                    verify_main(root)
            runtime.supervisor.sync_ports()
            if all_merged and runtime.scheduler.machine.call(
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
                "provider":
                "codex" if real else "fake",
                "profiles": [
                    list(profile) for profile in sorted({(e["model"], e["effort"])
                                                         for identifier in states
                                                         for job in states[identifier]
                                                         for e in runtime.snapshot(
                                                             identifier, job["id"])["executions"]})
                ],
                "pipeline":
                pipeline,
                "jobs":
                6,
                "attempts":
                len(attempts),
                "wall_seconds":
                round(time.monotonic() - started, 2),
                "peak_live":
                peak_live,
                "peak_repos":
                peak_repos,
                "leases": [list(row) for row in sorted(lease_history)],
                "all_merged":
                all_merged,
                "failures": [list(row) for row in failed],
                "resumed":
                resume,
                "ignore_user_config":
                real,
                "claude_parity":
                "pending session 1.11"
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
    """Parse UTC timestamps from v1 history or v2 attempts."""
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
    """Read a v2 suite/repo, or legacy v1 jobs without modifying evidence."""
    databases = sorted(sandbox.glob("*/factory/state/factory.db"))
    if (sandbox / "factory/state/factory.db").is_file():
        databases = [sandbox / "factory/state/factory.db"]
    if databases:
        return [row for path in databases for row in _engine_benchmark_rows(path)]
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


def _sum_known(values: list[float | int | None]) -> float | int | None:
    return sum(values) if values and all(value is not None for value in values) else None


def _elapsed(start: str | None, end: str | None) -> float | None:
    a, b = _timestamp(start), _timestamp(end)
    return b - a if a is not None and b is not None and b >= a else None


def _engine_benchmark_rows(path: Path) -> list[dict[str, Any]]:
    # Avoid migrations, directory creation and WAL pragma changes during measurement.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = []
    try:
        for job in connection.execute("SELECT * FROM jobs ORDER BY seq"):
            attempts = [
                dict(row) for row in connection.execute(
                    "SELECT a.*, e.step_id, e.provider, e.model, e.effort FROM attempts a "
                    "JOIN executions e ON e.id=a.execution_id WHERE e.job_id=? ORDER BY a.id", (
                        job["id"], ))
            ]
            wall = (_elapsed(job["created_at"], job["updated_at"])
                    if job["state"] in {"approved", "merged", "cancelled", "needs_human"} else None)
            process = [_elapsed(a["t_launch"], a["t_exit"]) for a in attempts]
            stages = {}
            for stage in dict.fromkeys(a["step_id"] for a in attempts):
                stages[stage] = _sum_known(
                    [duration for a, duration in zip(attempts, process) if a["step_id"] == stage])
            usage = [json.loads(a["usage"]) if a["usage"] else {} for a in attempts]
            totals = {
                key: _sum_known([u.get(key) for u in usage])
                for key in ("input", "cached_input", "output", "cost_usd")
            }
            process_total = _sum_known(process)
            rows.append({
                "id":
                job["id"],
                "feature":
                job["title"],
                "wall":
                wall,
                "stages":
                stages,
                "agents":
                len(attempts),
                "tokens":
                _sum_known([totals["input"], totals["output"]]),
                "cost":
                totals["cost_usd"],
                "repo":
                path.parents[2].name,
                "pipeline":
                job["pipeline_name"],
                "state":
                job["state"],
                "profiles":
                sorted({(a["provider"], a["model"], a["effort"])
                        for a in attempts}),
                "input":
                totals["input"],
                "cached_input":
                totals["cached_input"],
                "output":
                totals["output"],
                "process":
                process_total,
                "overhead":
                wall - process_total if wall is not None and process_total is not None else None,
                "startup": [_elapsed(a["t_launch"], a["t_first_event"]) for a in attempts],
                "validation": [_elapsed(a["t_exit"], a["t_validated"]) for a in attempts],
                "packet_bytes":
                _sum_known([a["packet_bytes"] for a in attempts]),
            })
    finally:
        connection.close()
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


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _number(value: float | int | None) -> str:
    return f"{value:.0f}" if value is not None else "unknown"


def _durations(values: list[float | None]) -> str:
    return ", ".join(_duration(value) for value in values) or "unknown"


def _engine_markdown(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        lines.extend([
            f"### {_cell(row['repo'])} / {_cell(row['pipeline'])} / {_cell(row['id'])}", "",
            f"State: {row['state']}; profiles: {_cell(row['profiles'])}.", "",
            _benchmark_markdown([row]), "",
            "| Input | Cached input | Output | Process | Engine overhead | Startup per attempt | Validation per attempt | Packet bytes |",
            "|---:|---:|---:|---:|---:|---|---|---:|",
            f"| {_number(row['input'])} | {_number(row['cached_input'])} | {_number(row['output'])} | "
            f"{_duration(row['process'])} | {_duration(row['overhead'])} | "
            f"{_durations(row['startup'])} | {_durations(row['validation'])} | {_number(row['packet_bytes'])} |",
            ""
        ])
    lines.extend([
        "Stage times sum CLI process durations, including retries and superseded executions; parallel stages overlap.",
        "Engine overhead is wall minus the sum of CLI durations (§14); it can be negative for parallel Full reviews.",
        "Tokens = input + output; cached input is a subset of input and is shown separately.",
        "Packet bytes are summed across attempts. Missing observations remain unknown."
    ])
    return "\n".join(lines)


def _comparison_markdown(rows: list[dict[str, Any]]) -> str:
    groups: dict[tuple, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        if row.get("pipeline") in {"fast", "full"}:
            key = (row["repo"], row["feature"], tuple(map(tuple, row["profiles"])))
            groups.setdefault(key, {}).setdefault(row["pipeline"], []).append(row)
    lines = [
        "### Fast vs Full", "",
        "Pairs require the same repo, feature and provider/model/effort. Ratios require both jobs approved/merged; duplicates are ambiguous.",
        "",
        "| Repo / feature / profiles | Fast wall | Full wall | Fast / Full wall | Fast tokens | Full tokens | Fast / Full tokens | Fast calls | Full calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    ]
    for (repo, feature, profiles), pipelines in sorted(groups.items()):
        fast, full = (pipelines.get(name, []) for name in ("fast", "full"))
        a, b = (values[0] if len(values) == 1 else {} for values in (fast, full))

        def ratio(key: str) -> str:
            if (a.get("state") in {"approved", "merged"}
                    and b.get("state") in {"approved", "merged"} and a.get(key) is not None
                    and b.get(key)):
                return f"{a[key] / b[key]:.1%}"
            return "unknown"

        lines.append(
            f"| {_cell((repo, feature, profiles))} | {_duration(a.get('wall'))} | "
            f"{_duration(b.get('wall'))} | {ratio('wall')} | {_number(a.get('tokens'))} | "
            f"{_number(b.get('tokens'))} | {ratio('tokens')} | {_number(a.get('agents'))} | {_number(b.get('agents'))} |"
        )
    return "\n".join(lines)


def bench(sandbox: Path, *, compare: list[Path] | None = None) -> None:
    if not sandbox.exists():
        sys.exit("no sandbox yet; run: python tools/sandbox.py new")
    sources = list(dict.fromkeys([sandbox.resolve(), *(p.resolve() for p in compare or [])]))
    if any(not source.exists() for source in sources):
        raise ValueError("benchmark source does not exist")
    rows = [row for source in sources for row in benchmark_rows(source)]
    engine = [row for row in rows if "pipeline" in row]
    table = (_engine_markdown(engine) + "\n\n" +
             _comparison_markdown(engine) if engine else _benchmark_markdown(rows))
    print(table)
    target = ROOT / "docs" / "benchmarks.md"
    heading = "## v2 benchmark" if engine else "## v1 baseline"
    existing = target.read_text(encoding="utf-8") if target.exists() else "# Benchmarks\n"
    capture = (heading + " — " + datetime.now().astimezone().isoformat(timespec="seconds") +
               "\n\nSources: " + ", ".join(f"`{p}`" for p in sources) + "\n\n" + table + "\n")
    existing = existing.rstrip() + "\n\n" + capture
    target.write_text(existing, encoding="utf-8")
    print(f"\nWrote {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["new", "run", "status", "clean", "bench"])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument(
        "--real",
        action="store_true",
        help="run Codex with the suite's configured profiles instead of the fake CLI")
    parser.add_argument("--timeout",
                        type=float,
                        default=3600,
                        help="whole-suite deadline in seconds")
    parser.add_argument("--ports",
                        help="machine port range; use distinct ranges for independent suites")
    parser.add_argument("--resume",
                        action="store_true",
                        help="continue an existing suite, resuming paused jobs")
    parser.add_argument("--pipeline", choices=["fast", "full"], default="full")
    parser.add_argument("--keep-going",
                        action="store_true",
                        help="finish independent benchmark jobs after another needs intervention")
    parser.add_argument("--model", default="gpt-6-luna", help="Codex model for new suites")
    parser.add_argument("--effort", default="low", help="Codex effort for new suites")
    parser.add_argument("--compare", type=Path, action="append", help="additional bench suite/repo")
    args = parser.parse_args()
    suite = Path(os.path.expandvars(str(args.dir))).expanduser().resolve()
    if suite == ROOT or ROOT in suite.parents or suite == Path(suite.anchor):
        parser.error("the sandbox must be outside this repo and not a filesystem root")
    if args.command == "run":
        run(suite,
            real=args.real,
            timeout=args.timeout,
            port_range=args.ports,
            resume=args.resume,
            pipeline=args.pipeline,
            keep_going=args.keep_going)
    elif args.command == "new":
        new(suite, model=args.model, effort=args.effort)
    elif args.command == "bench":
        bench(suite, compare=args.compare)
    else:
        {"status": status, "clean": clean}[args.command](suite)


if __name__ == "__main__":
    main()
