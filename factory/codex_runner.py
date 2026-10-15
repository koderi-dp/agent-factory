"""Run a headless Codex factory session and retain its exact ID for recovery.

  python factory/codex_runner.py run <session-file> --permissions auto
  python factory/codex_runner.py resume <session-file>

The dashboard creates these session files under factory/jobs/. Codex uses its
normal saved login. JSONL events go to stdout (the dashboard redirects to a log).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import job


def command(repo, permissions, session_id=None):
    executable = shutil.which("codex")
    if not executable:
        raise job.JobError("the codex command isn't on PATH; install Codex CLI and sign in first")
    worktrees = repo.parent / f"{repo.name}-factory"
    worktrees.mkdir(exist_ok=True)
    args = [executable, "exec"]
    if permissions == "auto":
        # --approve-for-me selects workspace-write itself; --sandbox conflicts with it.
        args += ["--approve-for-me", "--add-dir", str(worktrees)]
    elif permissions == "bypassPermissions":
        args += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        raise job.JobError(f"unknown permission mode: {permissions}")
    if session_id:
        if not re.fullmatch(r"[a-zA-Z0-9-]+", session_id) or session_id.startswith("-"):
            raise job.JobError("invalid Codex session ID")
        args += ["resume", "--json", session_id,
                 "Continue the factory session. If a job was interrupted, use $factory resume for that job after checking its state; otherwise use $factory watch --once."]
    else:
        args += ["--json", "$factory watch --once. Use the installed factory skill and delegate its roles to subagents."]
    return args


def run(path, permissions=None, resume=False):
    repo = job.repo_root()
    if resume:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("provider") != "codex" or not state.get("sessionId"):
            raise job.JobError("session file has no Codex session ID")
        if Path(state["repo"]).resolve() != repo.resolve():
            raise job.JobError("session belongs to a different project")
        # A process ID alone can't establish liveness across machines/restarts.
        # The caller must confirm the old process is stopped before resuming.
        permissions = state["permissions"]
    else:
        if path.exists():
            raise job.JobError("session file already exists; use resume to continue it")
        state = {"provider": "codex", "repo": str(repo), "permissions": permissions,
                 "sessionId": None, "created": job.now()}
    args = command(repo, permissions, state.get("sessionId") if resume else None)
    state.pop("exitCode", None)
    state.pop("error", None)
    state.update(status="running", updated=job.now())
    job.write_json(path, state)
    process = None
    try:
        process = subprocess.Popen(args, cwd=repo, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                                   text=True, encoding="utf-8", errors="replace")
        state["pid"] = process.pid
        job.write_json(path, state)
        for line in process.stdout:
            print(line, end="", flush=True)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
                state["sessionId"] = event["thread_id"]
                job.write_json(path, state)
        code = process.wait()
        process.stdout.close()
    except BaseException as exc:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            if process.stdout:
                process.stdout.close()
        state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                     error=str(exc), updated=job.now())
        job.write_json(path, state)
        raise
    state.update(status="finished" if code == 0 else "failed", exitCode=code, updated=job.now())
    job.write_json(path, state)
    return code


def main():
    # Python otherwise uses the Windows console code page even when the parent
    # redirects stdout into a UTF-8 log. Codex events and report text are Unicode.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "resume"))
    parser.add_argument("session_file", type=Path)
    parser.add_argument("--permissions", choices=sorted(job.HEADLESS_PERMISSIONS), default="auto")
    args = parser.parse_args()
    try:
        return run(args.session_file.resolve(), args.permissions, args.action == "resume")
    except (job.JobError, OSError, ValueError, KeyError) as exc:
        print(f"Codex factory failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
