"""Session 1.5 stub adapter and a small restartable service, using only fake_cli.

No provider-specific command building/classification is implemented here (§1.6).
"""

import json
from pathlib import Path
import sys
import time

from agent_factory import run_cli
from agent_factory.engine import pipeline, registry, reports
from agent_factory.engine.config import RepoConfig
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import EventUpdate, ExitOutcome, Prepared, Supervisor

FAKE_CLI = Path(__file__).with_name("fake_cli.py").resolve()


class StubAdapter:
    def __init__(self):
        self.seen = []

    def prepare(self, context):
        directory = context.directory
        settings = context.root / "scenario.json"
        scenario = json.loads(settings.read_text()) if settings.exists() else {}
        defaults = {
            "sleep": 0.1, "report": {"approval": "APPROVE\n", "review": "VERDICT: PASS\n",
                                      "task": "STATUS: READY\n"}[context.execution["type"]],
            "report_path": str(directory / "report.md"),
            "invocation_marker": str(context.root / "invocations"),
            "events": [{"session_id": "fake-session"}, {"activity": "working č"},
                       {"usage": {"input": 10, "output": 2}}],
        }
        scenario = {**defaults, **scenario}
        if "reports" in scenario:
            scenario["report"] = scenario["reports"][min(context.attempt["n"] - 1, len(scenario["reports"]) - 1)]
        run_cli.atomic_json(directory / "scenario.json", scenario)
        retry = None
        if context.previous:
            if context.attempt["kind"] == "contract_retry":
                retry = context.previous["error_detail"]
            elif context.previous["state"] == "lost" and context.attempt["kind"] == "fresh":
                retry = "The worktree may contain partial uncommitted work. Inspect it first."
        prompt = "Stub execution\n" + (retry or "")
        return Prepared(
            {"argv": [sys._base_executable, "-I", "-S", str(FAKE_CLI), "--scenario", str(directory / "scenario.json")],
             "cwd": context.job["worktree"], "env": {}, "stdin": "prompt.md"},
            prompt, context.job["frozen"]["preamble"], {"retry_context": retry})

    def parse_event(self, line):
        self.seen.append(line)
        try:
            event = json.loads(line)
            return EventUpdate(event.get("session_id"), event.get("usage"), event.get("activity"))
        except (ValueError, AttributeError):
            return EventUpdate()

    def classify(self, context, exit_info):
        if exit_info["exit_code"]:
            return ExitOutcome("failed", "crash", "fake failure")
        step = next(s for s in context.job["frozen"]["pipeline"] if s["id"] == context.execution["step_id"])
        entry = next(e for e in step["executions"] if e["id"] == context.execution["exec_id"])
        path = context.directory / "report.md"
        try:
            result = reports.read_result(path, entry["role"]["contract"],
                                         t_launch=context.attempt["t_launch"], on_changes=bool(step["onChanges"]))
        except reports.ReportError as error:
            return ExitOutcome("invalid", "contract", str(error))
        relative = context.attempt["dir"] + "/report.md"
        return ExitOutcome("valid", completion=pipeline.Completion(result.verdict, "head", relative), current_head="head")


def create_repo(root, *, jobs=1, parallel=0, per_job=4):
    root.mkdir(parents=True)
    roles = root / "factory/roles"
    roles.mkdir(parents=True)
    (roles / "gate.md").write_text("---\ntype: approval\n---\nApprove.\n")
    (roles / "review.md").write_text("---\ntype: review\n---\nReview.\n")

    def entry(identifier, uses):
        return {"id": identifier, "uses": uses, "provider": "codex",
                "profiles": {"codex": {"model": "fake", "effort": "low"}}}

    steps = [entry("approve", "gate")]
    if parallel:
        steps.insert(0, {"id": "reviews", "parallel": [entry(f"review-{i}", "review") for i in range(parallel)]})
    raw = {"version": 2, "engine": ">=0", "defaultPipeline": "test", "pipelines": {"test": steps},
           "concurrency": {"perJob": per_job}}
    (root / "factory/config.json").write_text(json.dumps(raw))
    config = RepoConfig.model_validate(raw, context={"root": root})
    connection = pipeline.open_repo(root)
    try:
        for n in range(1, jobs + 1):
            job_id = f"{n:03}-job"
            worktree = root / f"worktree-{n}"
            worktree.mkdir()
            pipeline.create_job(connection, job_id, "Test", config=config, worktree=str(worktree),
                                branch="factory/" + job_id, base_head="head", preamble="Frozen stub.\n",
                                preamble_version="test")
            pipeline.advance_job(connection, job_id, current_head="head")
    finally:
        connection.close()
    connection = registry.open_registry()
    try:
        return registry.register(connection, root).id
    finally:
        connection.close()


def main():
    scheduler = Scheduler()
    supervisor = Supervisor(scheduler, {"codex": StubAdapter()})
    try:
        supervisor.start()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            supervisor.tick()
            states = [writer.call(lambda connection: [dict(row) for row in connection.execute(
                "SELECT state,pause_reason FROM jobs")]) for _, writer in scheduler.repos.values()]
            if all(row["state"] != "running" for rows in states for row in rows):
                print(json.dumps(states), flush=True)
                return 0
            time.sleep(0.02)
        raise AssertionError("Fake service did not settle")
    finally:
        scheduler.close()


if __name__ == "__main__":
    raise SystemExit(main())
