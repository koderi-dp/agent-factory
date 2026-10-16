"""Serve the factory dashboard, with a small API for changing settings, jobs and the backlog.

  python factory/server.py [--port 5099]

It listens on 127.0.0.1 only. Every request must name this server in its Host
header, and every change must send `X-Factory: 1`, so other web pages can't
drive it. Without it, `python -m http.server` still serves a read-only board.

  GET  /api/config                 settings, resolved pipeline and provider model/effort choices
  PUT  /api/config                 replace the settings
  POST /api/jobs/<job-id>/merge    merge an approved or waiting job
  POST /api/jobs/<job-id>/delete   remove a job's worktree, branch and folder ({"force": true} for a live one)
  POST /api/backlog                {"action": "add", "items": [{"text": title, "details": markdown}, ...]},
                                   {"action": "remove", "index": n}, {"action": "move", "index": n, "to": m},
                                   {"action": "start" or "cancel", "index": n} for a /factory watch session;
                                   n and m count unchecked items. Details are stored indented under the item.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True  # no factory/__pycache__ in the repos the factory runs in
import job  # noqa: E402  factory/job.py; Python puts this script's folder on the path

BACKLOG = job.FACTORY / "backlog.md"
ITEM = re.compile(r"^[-*]\s+\[[ xX]\]\s+\S")
UNCHECKED = re.compile(r"^[-*]\s+\[ \]\s+\S")


def backlog_blocks(lines):
    """Split backlog.md into blocks: each item with the indented details under it, and everything else."""
    blocks = []
    i = 0
    while i < len(lines):
        if not ITEM.match(lines[i]):
            blocks.append({"open": False, "lines": [lines[i]]})
            i += 1
            continue
        block = [lines[i]]
        i += 1
        while i < len(lines) and (not lines[i].strip() or lines[i].startswith("  ")):
            block.append(lines[i])
            i += 1
        # Blank lines after the details separate items; they aren't part of one.
        tail = []
        while len(block) > 1 and not block[-1].strip():
            tail.insert(0, block.pop())
        blocks.append({"open": bool(UNCHECKED.match(block[0])), "lines": block})
        blocks.extend({"open": False, "lines": [l]} for l in tail)
    return blocks


def new_items(body):
    items = body.get("items")
    if items is None and "text" in body:
        items = [{"text": body["text"]}]
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise job.JobError("items must be a list of 1 to 50 features")
    out = []
    for item in items:
        title = item.get("text") if isinstance(item, dict) else None
        details = item.get("details", "") if isinstance(item, dict) else None
        if not isinstance(title,
                          str) or not title.strip() or "\n" in title.strip() or len(title) > 300:
            raise job.JobError("each item needs a one-line title of at most 300 characters")
        if not isinstance(details, str) or len(details) > 20_000:
            raise job.JobError("details must be text of at most 20,000 characters")
        lines = [f"- [ ] {title.strip()}"]
        # Details sit under the item, indented, so the backlog stays one Markdown list.
        lines += [f"  {l}".rstrip() if l.strip() else "" for l in details.strip("\n").splitlines()]
        out.append({"open": True, "lines": lines})
    return out


def start_headless(permissions):
    """Launch a background `/factory watch --once` session that takes the next start request."""
    provider = job.load_config()["provider"]
    executable = shutil.which(provider)
    if not executable:
        raise job.JobError(
            f"the {provider} command isn't on PATH, so Start can't launch a headless session")
    repo = job.repo_root()
    required = (repo / ".agents/skills/factory/SKILL.md" if provider == "codex" else repo /
                ".claude/skills/factory/SKILL.md")
    if not required.is_file():
        raise job.JobError(f"install the {provider} factory skill first: {required}")
    job.JOBS.mkdir(exist_ok=True)
    log = job.JOBS / f"headless-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10_000:04}.log"
    # Its own process group, so it keeps running if the dashboard server stops. On Windows it gets
    # one hidden console that every command it runs shares; with no console at all (DETACHED_PROCESS)
    # each git or python call would open a window of its own.
    flags = ({
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    } if os.name == "nt" else {
        "start_new_session": True
    })
    args = ([
        sys.executable,
        str(job.FACTORY / "codex_runner.py"), "run",
        str(log.with_suffix(".session.json")), "--permissions", permissions
    ] if provider == "codex" else
            [executable, "-p", "/factory watch --once", "--permission-mode", permissions])
    with open(log, "w", encoding="utf-8") as out:
        subprocess.Popen(args,
                         cwd=repo,
                         stdin=subprocess.DEVNULL,
                         stdout=out,
                         stderr=subprocess.STDOUT,
                         **flags)
    return log.name


def edit_backlog(body):
    text = BACKLOG.read_text(encoding="utf-8") if BACKLOG.exists() else "# Backlog\n\n"
    blocks = backlog_blocks(text.splitlines())
    slots = [i for i, b in enumerate(blocks) if b["open"]]
    action = body.get("action")

    def item_at(key):
        n = body.get(key)
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(slots):
            raise job.JobError(f"{key} must name one of the {len(slots)} queued items")
        return n

    if action in ("start", "cancel"):
        title = blocks[slots[item_at("index")]]["lines"][0][6:].strip()
        if action == "cancel":
            return job.cancel_request(title)
        queued = any(r["item"] == title for r in job.pending_requests())
        request = job.request_start(title)
        cfg = job.load_config()
        # A session that is already watching takes it; launch one only when none is ready.
        if not queued and cfg["start"] == "headless" and not job.idle_watchers(cfg["provider"]):
            try:
                request["headless"] = start_headless(cfg["headlessPermissions"])
            except (job.JobError, OSError) as exc:
                job.cancel_request(
                    title)  # leave Start retryable after the missing tool is installed
                raise job.JobError(str(exc))
        return request
    if action == "add":
        blocks.extend(new_items(body))
    elif action == "remove":
        del blocks[slots[item_at("index")]]
    elif action == "move":
        src, dst = item_at("index"), item_at("to")
        moved = [blocks[i] for i in slots]
        moved.insert(dst, moved.pop(src))
        for i, block in zip(slots, moved):
            blocks[i] = block
    else:
        raise job.JobError("action must be add, remove or move")
    tmp = BACKLOG.with_name("backlog.md.tmp")
    tmp.write_text("\n".join(l for b in blocks for l in b["lines"]) + "\n", encoding="utf-8")
    os.replace(tmp, BACKLOG)


class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(job.FACTORY), **kwargs)

    # Polling every two seconds would flood the console; log only changes and failures.
    def log_request(self, code="-", size="-"):
        if self.command != "GET" or str(code)[0] in "45":
            super().log_request(code, size)

    def host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            raise job.JobError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise job.JobError("body must be JSON")

    def do_GET(self):
        if not self.host_ok():
            return self.send_error(403, "wrong host")
        # The board lives in dashboard/; / and the old /dashboard.html both lead there.
        if self.path.split("?")[0] in ("/", "/dashboard.html"):
            self.send_response(302)
            self.send_header("Location", "/dashboard/")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if self.path.split("?")[0] == "/api/config":
            try:
                cfg = job.load_config()
                _, steps, registry = job.pipeline.resolve(cfg["pipeline"],
                                                          root=job.FACTORY.parent,
                                                          provider=cfg["provider"])
                choices = {
                    provider: {
                        "models": sorted(job.CODEX_MODELS if provider == "codex" else job.MODELS),
                        "efforts": list(job.EFFORTS),
                    }
                    for provider in sorted(job.PROVIDERS)
                }
                return self.send_json(
                    200, {
                        "config": cfg,
                        **choices[cfg["provider"]],
                        "choices": choices,
                        "providers": sorted(job.PROVIDERS),
                        "agents": list(registry),
                        "builtins": list(job.AGENTS),
                        "pipeline": steps,
                        "pipelineAgents": registry,
                    })
            except job.JobError as err:
                return self.send_json(500, {"error": str(err)})
        if self.path.startswith("/api/"):
            return self.send_json(404, {"error": "no such endpoint"})
        super().do_GET()

    def change(self):
        if not self.host_ok() or self.headers.get("X-Factory") != "1":
            return self.send_json(
                403, {"error": "changes need the X-Factory header from this server's page"})
        path = self.path.split("?")[0]
        try:
            body = self.read_body()
            if path == "/api/config" and self.command == "PUT":
                return self.send_json(200, {"config": job.save_config(body)})
            m = re.fullmatch(r"/api/jobs/([^/]+)/(merge|delete)", path)
            if m and self.command == "POST":
                job_id, action = m.groups()
                if action == "merge":
                    return self.send_json(200, {"message": job.merge(job_id, human=True)})
                return self.send_json(200, {"message": job.delete(job_id, bool(body.get("force")))})
            if path == "/api/backlog" and self.command == "POST":
                result = edit_backlog(body)
                return self.send_json(200, {
                    "ok": True,
                    **(result if isinstance(result, dict) else {})
                })
            return self.send_json(404, {"error": "no such endpoint"})
        except job.Busy as err:
            return self.send_json(409, {"error": str(err), "busy": True})
        except job.JobError as err:
            return self.send_json(400, {"error": str(err)})

    do_POST = change
    do_PUT = change


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5099)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"factory dashboard: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
