"""Read and change factory jobs and settings. The orchestrator and server.py use it.

  python factory/job.py new <job-id> --feature "<text>" --worktree <path>
  python factory/job.py set <job-id> key=value [key=value ...]
  python factory/job.py show <job-id>
  python factory/job.py merge <job-id>
  python factory/job.py delete <job-id> [--force]
  python factory/job.py config
  python factory/job.py step <job-id>          dispatch the current saved pipeline step
  python factory/job.py advance <job-id>       validate reports and transition
  python factory/job.py rework <job-id> --note "<text>"
  python factory/job.py wait --watcher <id> [--timeout 540]
  python factory/job.py unwatch --watcher <id>
  python factory/job.py free-ports <job-id>

Keys for set: stage, round, resumes, builder, feature, note, reviews.<lens>,
heads.<round>, and reviews=pending to reset all four. A change of stage appends a
history entry (with the round on review entries) and clears note unless the same
call sets it. Every write sets updated, holds the job's lock, and replaces the
file in one step, so the dashboard never reads half of it.

wait blocks until the dashboard's Start button queues a backlog item, claims it and
prints {"item": ...} (exit 0), or gives up after the timeout (exit 2).

merge exits 0 when merged, 3 when the main checkout is busy (not on main, something
staged, or uncommitted edits in files the branch changes; nothing changed) and 1
when the merge failed (aborted, nothing changed).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pipeline

FACTORY = Path(__file__).resolve().parent
JOBS = FACTORY / "jobs"
CONFIG = FACTORY / "config.json"
STAGES = {"spec", "build", "review", "approve", "approved", "merged", "needs-human"}
LIVE = {"spec", "build", "review", "approve"}
MERGEABLE = {"approve", "approved", "needs-human"}
LENSES = ("security", "ux", "ui", "code")
VERDICTS = {"pending", "PASS", "CHANGES"}
INTS = {"round", "resumes"}
STRINGS = {"stage", "builder", "feature", "note", "provider"}
AGENTS = ("spec-writer", "builder", "reviewer-security", "reviewer-ux", "reviewer-ui", "reviewer-code", "approver")
MODELS = pipeline.MODELS["claude"]
PROVIDERS = {"claude", "codex"}
CODEX_MODELS = pipeline.MODELS["codex"]
EFFORTS = pipeline.EFFORTS
MERGE_POLICIES = {"auto", "verify"}
START_MODES = {"queue", "headless"}
HEADLESS_PERMISSIONS = {"auto", "bypassPermissions"}
DEFAULT_CONFIG = {
    "pipeline": pipeline.DEFAULT,
    "provider": "claude",
    "merge": "auto",
    "maxRounds": 3,
    # What Start does when no /factory watch session is ready: wait for one, or launch a headless one.
    "start": "queue",
    # The permission mode of a headless session: auto lets a classifier approve routine commands.
    "headlessPermissions": "auto",
}


class JobError(Exception):
    pass


class Busy(JobError):
    """The main checkout can't take a merge right now."""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_of(job_id):
    if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
        raise JobError(f"bad job id: {job_id!r}")
    return JOBS / job_id / "job.json"


def write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked(job_id):
    lock = JOBS / job_id / ".lock"
    for _ in range(200):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # A lock older than 30 s belongs to a process that died holding it.
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.05)
    else:
        raise JobError(f"{job_id} is locked by another writer")
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def load(job_id):
    path = path_of(job_id)
    if not path.exists():
        raise JobError(f"no job.json for {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(job):
    job["updated"] = now()
    write_json(path_of(job["id"]), job)


def new(job_id, feature, worktree, provider="claude"):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise JobError(f"unknown provider: {provider}")
    if not (JOBS / job_id).is_dir():
        raise JobError(f"claim the job first: mkdir {JOBS / job_id}")
    with locked(job_id):
        if path_of(job_id).exists():
            raise JobError(f"{job_id} already has a job.json")
        at = now()
        try:
            frozen = pipeline.snapshot(load_config(provider=provider), FACTORY.parent, provider)
        except pipeline.PipelineError as exc:
            raise JobError(str(exc)) from exc
        for step in frozen["pipeline"]:
            for execution in step["executions"]:
                if provider not in execution["providers"]:
                    raise JobError(f"{step['id']}/{execution['id']}: missing {provider} settings")
        first = frozen["pipeline"][0]["id"]
        save({
            **frozen,
            "id": job_id,
            "feature": feature,
            "provider": provider,
            "branch": f"factory/{job_id}",
            "worktree": worktree.replace("\\", "/"),
            "stage": first,
            "round": 0,
            "resumes": 0,
            "reviews": {k: "pending" for k in LENSES},
            "heads": {},
            "created": at,
            "history": [{"stage": first, "at": at}],
        })


def apply(job, key, value):
    if key == "provider" and value != job["provider"]:
        raise JobError("a job's provider is fixed when it is created")
    if key == "reviews":
        if value != "pending":
            raise JobError("reviews=pending is the only whole-map value")
        job["reviews"] = {k: "pending" for k in LENSES}
    elif key.startswith("reviews."):
        lens = key.split(".", 1)[1]
        if lens not in {*LENSES, *job.get("pipelineAgents", {})} or value not in VERDICTS:
            raise JobError(f"bad review: {key}={value}")
        job.setdefault("reviews", {})[lens] = value
    elif key.startswith("heads."):
        job.setdefault("heads", {})[key.split(".", 1)[1]] = value
    elif key in INTS:
        job[key] = int(value)
    elif key in STRINGS:
        stages = pipeline.TERMINAL | {s["id"] for s in job["pipeline"]} if "pipeline" in job else STAGES
        if key == "stage" and value not in stages:
            raise JobError(f"unknown stage: {value}")
        job[key] = value
    else:
        raise JobError(f"unknown key: {key}")


def update(job_id, changes):
    """Apply (key, value) pairs to a job in one locked write."""
    with locked(job_id):
        job = load(job_id)
        before = job.get("stage")
        for key, value in changes:
            apply(job, key, value)
        if job.get("stage") != before:
            job.pop("activeRun", None)
            entry = {"stage": job["stage"], "at": now()}
            if job["stage"] == "review":
                entry["round"] = job.get("round", 0)
            job.setdefault("history", []).append(entry)
            if "note" not in dict(changes):
                job.pop("note", None)
        if job.get("note") == "":
            job.pop("note")
        save(job)
        return job


# ---- Pipeline execution -------------------------------------------------------


def move(job, stage, note=None):
    job["stage"] = stage
    job.pop("activeRun", None)
    job.pop("note", None)
    if note:
        job["note"] = note
    job.setdefault("history", []).append({"stage": stage, "round": job.get("round", 0), "at": now()})


def branch_head(job, clean=False):
    worktree = Path(job["worktree"])
    current = git("branch", "--show-current", cwd=worktree)
    if current.returncode or current.stdout.strip() != job["branch"]:
        raise JobError("the job worktree must be on " + job["branch"])
    if clean:
        status = git("status", "--porcelain", cwd=worktree)
        if status.returncode:
            raise JobError("could not inspect the job worktree: " + status.stderr.strip())
        if status.stdout.strip():
            raise JobError("the job worktree has uncommitted changes; commit the step's changes first")
    head = git("rev-parse", "HEAD", cwd=worktree)
    if head.returncode:
        raise JobError("could not read the job branch head: " + head.stderr.strip())
    return head.stdout.strip()


def report_signature(path):
    if not path.exists():
        return None
    return [path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()]


def prepare_run(job):
    step = next((s for s in job["pipeline"] if s["id"] == job["stage"]), None)
    if step is None:
        return None
    for prior in job["pipeline"][:job["pipeline"].index(step)]:
        result = job["results"].get(prior["id"])
        if result is None or any(r["verdict"] not in {"READY", "PASS"} for r in result.get("reports", {}).values()):
            raise JobError(f"finish prerequisite step {prior['id']} before {step['id']}")
    if "activeRun" in job:
        return step
    head = branch_head(job, clean=step["type"] != "task")
    if step["type"] == "review":
        job["round"] += 1
        job["reviews"] = {a: "pending" for a in step["agents"]}
        job.setdefault("heads", {})[str(job["round"])] = head
    attempt = job.setdefault("attempts", {}).get(step["id"], 0) + 1
    job["attempts"][step["id"]] = attempt
    run = {"step": step["id"], "attempt": attempt, "round": job["round"], "head": head,
           "started": now(), "before": {}}
    for name in step["agents"]:
        path = pipeline.report_path(path_of(job["id"]).parent, step, job["pipelineAgents"][name], run)
        path.parent.mkdir(parents=True, exist_ok=True)
        run["before"][name] = report_signature(path)
    job["activeRun"] = run
    return step


def read_result(job, step, name):
    agent = job["pipelineAgents"][name]
    run = job["activeRun"]
    folder = path_of(job["id"]).parent
    path = pipeline.report_path(folder, step, agent, run)
    if report_signature(path) == run["before"][name]:
        raise JobError(f"{name} has not written a fresh report: {path}")
    try:
        verdict = pipeline.verdict(agent, path.read_text(encoding="utf-8"))
    except (pipeline.PipelineError, FileNotFoundError) as exc:
        raise JobError(str(exc))
    return {"verdict": verdict, "file": path.relative_to(folder).as_posix()}


def step_plan(job_id):
    """Return the pending agent tasks; repeated calls preserve the same attempt."""
    with locked(job_id):
        job = load(job_id)
        step = prepare_run(job)
        if step is None:
            return {"stage": job["stage"], "tasks": [], "note": job.get("note"),
                    "merge": job["stage"] == "approved" and job.get("mergePolicy") == "auto"}
        folder, repo = path_of(job_id).parent, repo_root()
        executions = {e["uses"]: e for e in step["executions"]}
        tasks = []
        for name in step["agents"]:
            agent = job["pipelineAgents"][name]
            execution = executions[name]
            provider = job.get("provider", "claude")
            selection = execution["providers"].get(provider)
            if selection is None:
                raise JobError(f"{step['id']}/{execution['id']}: missing {provider} settings")
            try:
                result = read_result(job, step, name)
            except JobError:
                result = None
            if step["type"] == "review" and result:
                job["reviews"][name] = result["verdict"]
            if result:
                continue  # recover completed parallel reviewers after interruption
            try:
                instructions = pipeline.inside(repo, agent["instructions"])
            except pipeline.PipelineError as exc:
                raise JobError(str(exc)) from exc
            if not instructions.is_file():
                raise JobError(f"missing instructions for {name}: {instructions}")
            ports = job_ports(job_id)
            offset = 3 if name == "reviewer-ux" else 6 if name == "reviewer-ui" else 0
            assigned = ports[offset:offset + 3] if name in {"builder", "spec-writer", "reviewer-ux", "reviewer-ui"} or step["type"] == "task" else []
            tasks.append({"id": execution["id"], "agent": name, "type": step["type"], "instructions": str(instructions),
                          "output": str(pipeline.report_path(folder, step, agent, job["activeRun"])),
                          "model": selection["model"], "effort": selection["effort"],
                          "ports": assigned, "contract": agent["contract"]})
        save(job)
        return {"stage": job["stage"], "step": step, "run": job["activeRun"],
                "worktree": job["worktree"], "branch": job["branch"], "jobFolder": str(folder),
                "tasks": tasks, "results": job["results"], "feature": job["feature"],
                "rework": job.get("rework"), "merge": False}


def advance(job_id):
    """Validate all reports and advance, retry or pause. Never runs or merges agents."""
    with locked(job_id):
        job = load(job_id)
        if not job.get("activeRun"):
            raise JobError("run job.py step first")
        step = next(s for s in job["pipeline"] if s["id"] == job["stage"])
        head = branch_head(job, clean=True)
        if step["type"] != "task" and head != job["activeRun"]["head"]:
            raise JobError("the branch changed while this step was running; rework and review it again")
        if step["type"] == "approval":
            for reviewed in job["pipeline"]:
                result = job["results"].get(reviewed["id"], {})
                if reviewed["type"] == "review" and result.get("head") and result["head"] != head:
                    raise JobError(f"the branch changed after {reviewed['id']}; rework so its reviewers check the current head")
        reports = {name: read_result(job, step, name) for name in step["agents"]}
        # Keep immutable evidence even when a later attempt overwrites build.md or decision.md.
        folder = path_of(job_id).parent
        for name, report in reports.items():
            source = folder / report["file"]
            archived = pipeline.inside(folder, f"steps/{step['id']}/{job['activeRun']['attempt']}/{source.name}")
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, archived)
            report["archive"] = archived.relative_to(folder).as_posix()
        result = {**job["activeRun"], "head": head, "reports": reports, "finished": now()}
        job["runs"].append(result)
        job["results"][step["id"]] = result
        values = {r["verdict"] for r in reports.values()}
        if step["type"] == "review":
            job["reviews"] = {a: r["verdict"] for a, r in reports.items()}
        if "BLOCKED" in values:
            move(job, "needs-human", f"{step['id']} is blocked; see its report")
        elif "CHANGES" in values:
            target = step.get("onChanges")
            if target and job["resumes"] < job["maxRounds"] - 1:
                job["resumes"] += 1
                rewind(job, target)
            else:
                move(job, "needs-human", f"{step['id']} requested changes; no retry target or retry budget remaining")
        elif step["type"] == "approval":
            job["approval"] = {"verdict": next(iter(values)), "head": head, "file": next(iter(reports.values()))["file"]}
            job["pipelineComplete"] = True
            move(job, "approved" if "APPROVE" in values else "needs-human",
                 None if "APPROVE" in values else "the approval step escalated; see its decision")
        else:
            if "builder" in step["agents"] and not git("log", "--oneline", "main..HEAD", cwd=job["worktree"]).stdout.strip():
                raise JobError("the builder reported READY but the branch has no commits")
            index = job["pipeline"].index(step)
            move(job, job["pipeline"][index + 1]["id"])
        save(job)
        return job


def rewind(job, target):
    index = next(i for i, s in enumerate(job["pipeline"]) if s["id"] == target)
    for step in job["pipeline"][index:]:
        job["results"].pop(step["id"], None)
    job.pop("approval", None)
    job.pop("pipelineComplete", None)
    move(job, target)


def rework(job_id, note):
    with locked(job_id):
        job = load(job_id)
        if job["stage"] not in {"approved", "needs-human"}:
            raise JobError("stop or finish the current job before reworking it")
        target = next((s["id"] for s in job["pipeline"] if "builder" in s["agents"]), None)
        target = target or next((s["id"] for s in job["pipeline"] if s["type"] == "task"), None)
        if target is None:
            raise JobError("this pipeline has no task step to rework")
        for prior in job["pipeline"][:next(i for i, s in enumerate(job["pipeline"]) if s["id"] == target)]:
            if prior["id"] not in job["results"]:
                target = prior["id"]
                break
        number = len(list(path_of(job_id).parent.glob("rework-*.md"))) + 1
        report = path_of(job_id).parent / f"rework-{number}.md"
        report.write_text(note + "\n", encoding="utf-8")
        job.update(rework=report.name, resumes=0)
        rewind(job, target)
        save(job)
        return job


# ---- Git ---------------------------------------------------------------------


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def repo_root():
    r = git("rev-parse", "--show-toplevel", cwd=FACTORY)
    if r.returncode:
        raise JobError("factory/ is not inside a git repository")
    return Path(r.stdout.strip())


def branch_exists(repo, branch):
    return git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo).returncode == 0


def job_ports(job_id):
    """The block of ports the skill gives a job's agents: 5100 + 10n to 5108 + 10n."""
    try:
        n = int(job_id.split("-", 1)[0])
    except ValueError:
        return []
    return list(range(5100 + 10 * n, 5109 + 10 * n))


def listeners(ports):
    """Process ids listening on any of these local ports."""
    if os.name == "nt":
        script = ("Get-NetTCPConnection -State Listen -LocalPort " + ",".join(map(str, ports))
                  + " -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    else:
        r = subprocess.run(["lsof", "-ti", ",".join(f"tcp:{p}" for p in ports), "-sTCP:LISTEN"],
                           capture_output=True, text=True)
    return sorted({int(x) for x in r.stdout.split() if x.isdigit() and int(x) > 0})


def free_ports(job_id):
    """Stop whatever the job's agents left listening on its ports, such as a dev server.

    Agents are told to stop what they start, but a forgotten server keeps files in the worktree
    open, and on Windows that stops git from removing it.
    """
    ports = job_ports(job_id)
    stopped = listeners(ports) if ports else []
    for pid in stopped:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            subprocess.run(["kill", str(pid)], capture_output=True)
    if stopped:
        time.sleep(0.5)  # let Windows release the files
    return stopped


def merge(job_id, human=False):
    """Merge an approved or waiting job into main from the main checkout. Returns a message."""
    job = load(job_id)
    if job["stage"] not in MERGEABLE:
        raise JobError(f"{job_id} is in {job['stage']}; only an approved or waiting job can be merged")
    repo = repo_root()
    branch = job["branch"]
    if "pipeline" in job:
        approval = job.get("approval", {})
        if not job.get("pipelineComplete") or (approval.get("verdict") != "APPROVE" and not human):
            raise JobError("finish the configured pipeline with APPROVE before merging this job")
        if git("rev-parse", branch, cwd=repo).stdout.strip() != approval.get("head"):
            raise JobError("the branch changed after approval; rework and review it again")
    if not branch_exists(repo, branch):
        raise JobError(f"branch {branch} doesn't exist")
    current = git("branch", "--show-current", cwd=repo).stdout.strip()
    if current != "main":
        raise Busy(f"the main checkout is on {current or 'a detached HEAD'}, not main")
    # git merge refuses to run with anything staged, so a merge commit never picks up unrelated work.
    if git("diff", "--cached", "--quiet", cwd=repo).returncode:
        raise Busy("the main checkout has staged changes; commit or unstage them first")
    # Uncommitted edits elsewhere are safe: the merge and an abort only touch the files the branch changes.
    ours = set(git("diff", "--name-only", cwd=repo).stdout.splitlines())
    theirs = set(git("diff", "--name-only", f"main...{branch}", cwd=repo).stdout.splitlines())
    clash = sorted(ours & theirs)
    if clash:
        raise Busy("you have uncommitted changes in files this job also changes: " + ", ".join(clash[:5])
                   + ("…" if len(clash) > 5 else "") + ". Commit or stash them first")
    r = git("merge", "--no-ff", branch, "-m", f"Merge {branch}", cwd=repo)
    if r.returncode:
        if git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=repo).returncode == 0:
            git("merge", "--abort", cwd=repo)
        raise JobError("git merge failed and was undone: " + (r.stdout + r.stderr).strip()[-400:])
    notes = []
    worktree = job.get("worktree")
    free_ports(job_id)
    if worktree and Path(worktree).exists():
        r = git("worktree", "remove", worktree, cwd=repo)
        if r.returncode:
            notes.append("worktree not removed: " + r.stderr.strip())
    r = git("branch", "-d", branch, cwd=repo)
    if r.returncode:
        notes.append("branch not deleted: " + r.stderr.strip())
    changes = [("stage", "merged")] + ([("note", "; ".join(notes))] if notes else [])
    update(job_id, changes)
    return f"merged {branch} into main" + (f" ({'; '.join(notes)})" if notes else "")


def delete(job_id, force=False):
    """Remove a job's worktree, branch and folder. Returns a message."""
    folder = path_of(job_id).parent
    if not folder.is_dir():
        raise JobError(f"no job {job_id}")
    job = load(job_id) if path_of(job_id).exists() else {"id": job_id, "stage": None}
    if (pipeline.active(job) if "pipeline" in job else job["stage"] in LIVE) and not force:
        raise JobError(f"{job_id} is still running ({job['stage']}); stop its session first, or pass --force")
    removed = []
    if free_ports(job_id):
        removed.append("leftover servers")
    if job["stage"] != "merged":
        repo = repo_root()
        worktree = job.get("worktree")
        if worktree and Path(worktree).exists():
            r = git("worktree", "remove", "--force", worktree, cwd=repo)
            if r.returncode:
                raise JobError("couldn't remove the worktree: " + r.stderr.strip())
            removed.append("worktree")
        branch = job.get("branch", f"factory/{job_id}")
        if branch_exists(repo, branch):
            r = git("branch", "-D", branch, cwd=repo)
            if r.returncode:
                raise JobError("couldn't delete the branch: " + r.stderr.strip())
            removed.append("branch")
    shutil.rmtree(folder)
    removed.append("job folder")
    return f"deleted {job_id}: " + ", ".join(removed)


# ---- Starting jobs from the dashboard -----------------------------------------
#
# The dashboard can't type into a Claude Code session, so a session running /factory watch
# waits in `wait`. The dashboard's Start button queues a request file; `wait` claims it (a
# rename, so only one watcher gets it) and hands the backlog item to the session. Both kinds
# of file live in jobs/, which is gitignored, and the board tells them from job folders by name.

BEAT_SECONDS = 10


def pending_requests():
    out = []
    for path in sorted(JOBS.glob("request-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, FileNotFoundError):
            continue
        data["file"] = path.name
        out.append(data)
    return out


def request_start(item):
    """Queue a backlog item for a watching session to start. Returns the request."""
    if not isinstance(item, str) or not item.strip() or "\n" in item:
        raise JobError("a start request names one backlog item")
    for r in pending_requests():
        if r["item"] == item:
            return r
    JOBS.mkdir(exist_ok=True)
    request = {"item": item, "at": now(), "provider": load_config()["provider"]}
    write_json(JOBS / f"request-{time.time_ns()}.json", request)
    return request


def cancel_request(item):
    for r in pending_requests():
        if r["item"] == item:
            (JOBS / r["file"]).unlink(missing_ok=True)


def watcher_path(watcher):
    if not watcher or not all(c.isalnum() or c == "-" for c in watcher):
        raise JobError(f"bad watcher id: {watcher!r}")
    return JOBS / f"watcher-{watcher}.json"


def wait(watcher, timeout, provider="claude"):
    """Block until a start request arrives and claim it, or return None after timeout seconds."""
    beat = watcher_path(watcher)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise JobError(f"unknown provider: {provider}")
    JOBS.mkdir(exist_ok=True)
    end = time.time() + timeout
    last_beat = 0
    while time.time() < end:
        if time.time() - last_beat >= BEAT_SECONDS:
            write_json(beat, {"watcher": watcher, "at": now(), "busy": None, "provider": provider})
            last_beat = time.time()
        for r in pending_requests():
            if r.get("provider", "claude") != provider:
                continue
            src = JOBS / r["file"]
            claim = src.with_suffix(".claim")
            try:
                handle = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(handle)
            except FileExistsError:
                continue  # another watcher took it
            try:
                if not src.exists():
                    continue
                try:
                    src.unlink()
                except (FileNotFoundError, PermissionError):
                    continue  # cancelled, or a Windows reader still has it open; retry later
            finally:
                claim.unlink(missing_ok=True)
            write_json(beat, {"watcher": watcher, "at": now(), "busy": r["item"], "provider": provider})
            return {"item": r["item"], "at": r["at"]}
        time.sleep(1)
    return None


def unwatch(watcher):
    watcher_path(watcher).unlink(missing_ok=True)


def idle_watchers(provider=None):
    """Watch sessions waiting for a request right now."""
    out = []
    for path in JOBS.glob("watcher-*.json"):
        try:
            beat = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - datetime.strptime(beat["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, KeyError, FileNotFoundError):
            continue
        if (provider is None or beat.get("provider", "claude") == provider) and not beat.get("busy") and age < 3 * BEAT_SECONDS:
            out.append(beat["watcher"])
    return out


# ---- Settings ----------------------------------------------------------------


def check_config(cfg):
    if not isinstance(cfg, dict):
        raise JobError("settings must be an object")
    unknown = set(cfg) - set(DEFAULT_CONFIG)
    if unknown:
        raise JobError(f"unknown settings: {', '.join(sorted(unknown))}")
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    provider = cfg.get("provider", "claude")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise JobError(f"provider must be one of {sorted(PROVIDERS)}")
    out["provider"] = provider
    try:
        raw, _, _ = pipeline.resolve(cfg.get("pipeline"), root=FACTORY.parent, provider=provider)
    except pipeline.PipelineError as exc:
        raise JobError(str(exc)) from exc
    out["pipeline"] = raw
    if "merge" in cfg:
        if cfg["merge"] not in MERGE_POLICIES:
            raise JobError(f"merge must be one of {sorted(MERGE_POLICIES)}")
        out["merge"] = cfg["merge"]
    if "start" in cfg:
        if cfg["start"] not in START_MODES:
            raise JobError(f"start must be one of {sorted(START_MODES)}")
        out["start"] = cfg["start"]
    if "headlessPermissions" in cfg:
        if cfg["headlessPermissions"] not in HEADLESS_PERMISSIONS:
            raise JobError(f"headlessPermissions must be one of {sorted(HEADLESS_PERMISSIONS)}")
        out["headlessPermissions"] = cfg["headlessPermissions"]
    if "maxRounds" in cfg:
        rounds = cfg["maxRounds"]
        if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 5:
            raise JobError("maxRounds must be a whole number from 1 to 5")
        out["maxRounds"] = rounds
    return out


def load_config(provider=None):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if provider is not None:
        cfg["provider"] = provider
    return check_config(cfg)


def save_config(cfg):
    cfg = check_config(cfg)
    write_json(CONFIG, cfg)
    return cfg


# ---- CLI ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new")
    p.add_argument("job_id")
    p.add_argument("--feature", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="claude")
    p = sub.add_parser("set")
    p.add_argument("job_id")
    p.add_argument("pairs", nargs="+")
    for name in ("show", "step", "advance"):
        sub.add_parser(name).add_argument("job_id")
    p = sub.add_parser("merge")
    p.add_argument("job_id")
    p.add_argument("--human", action="store_true", help="Explicitly accept an escalated final approval")
    p = sub.add_parser("rework")
    p.add_argument("job_id")
    p.add_argument("--note", required=True)
    p = sub.add_parser("delete")
    p.add_argument("job_id")
    p.add_argument("--force", action="store_true")
    sub.add_parser("config")
    p = sub.add_parser("wait")
    p.add_argument("--watcher", required=True)
    p.add_argument("--timeout", type=int, default=540)
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="claude")
    sub.add_parser("unwatch").add_argument("--watcher", required=True)
    sub.add_parser("free-ports").add_argument("job_id")
    args = parser.parse_args()
    try:
        if args.command == "new":
            new(args.job_id, args.feature, args.worktree, args.provider)
        elif args.command == "set":
            changes = []
            for pair in args.pairs:
                key, sep, value = pair.partition("=")
                if not sep:
                    raise JobError(f"expected key=value, got {pair}")
                changes.append((key, value))
            update(args.job_id, changes)
        elif args.command == "show":
            print(json.dumps(load(args.job_id), indent=2, ensure_ascii=False))
        elif args.command == "merge":
            print(merge(args.job_id, args.human))
        elif args.command == "step":
            print(json.dumps(step_plan(args.job_id), indent=2, ensure_ascii=False))
        elif args.command == "advance":
            print(json.dumps(advance(args.job_id), indent=2, ensure_ascii=False))
        elif args.command == "rework":
            print(json.dumps(rework(args.job_id, args.note), indent=2, ensure_ascii=False))
        elif args.command == "delete":
            print(delete(args.job_id, args.force))
        elif args.command == "config":
            print(json.dumps(load_config(), indent=2))
        elif args.command == "wait":
            request = wait(args.watcher, args.timeout, args.provider)
            if request is None:
                print("no start request yet", file=sys.stderr)
                sys.exit(2)
            print(json.dumps(request, ensure_ascii=False))
        elif args.command == "unwatch":
            unwatch(args.watcher)
        elif args.command == "free-ports":
            stopped = free_ports(args.job_id)
            print(f"stopped {len(stopped)} process(es) on ports {job_ports(args.job_id)[0]}-{job_ports(args.job_id)[-1]}"
                  if job_ports(args.job_id) else "no ports for that job id")
    except Busy as err:
        print(f"busy: {err}", file=sys.stderr)
        sys.exit(3)
    except (JobError, pipeline.PipelineError) as err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
