"""Serve the factory dashboard, with a small API for changing settings, jobs and the backlog.

  python factory/server.py [--port 5099]

It listens on 127.0.0.1 only. Every request must name this server in its Host
header, and every change must send `X-Factory: 1`, so other web pages can't
drive it. Without it, `python -m http.server` still serves a read-only board.

  GET  /api/config                 settings, the model and effort each agent file names, and the choices
  PUT  /api/config                 replace the settings
  POST /api/jobs/<job-id>/merge    merge an approved or waiting job
  POST /api/jobs/<job-id>/delete   remove a job's worktree, branch and folder ({"force": true} for a live one)
  POST /api/backlog                {"action": "add", "items": [{"text": title, "details": markdown}, ...]},
                                   {"action": "remove", "index": n} or {"action": "move", "index": n, "to": m};
                                   n and m count unchecked items. Details are stored indented under the item.
"""

import argparse
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import job  # factory/job.py; Python puts this script's folder on the path

BACKLOG = job.FACTORY / "backlog.md"
ITEM = re.compile(r"^[-*]\s+\[[ xX]\]\s+\S")
UNCHECKED = re.compile(r"^[-*]\s+\[ \]\s+\S")


def agent_defaults(field, absent):
    """What each agent file's frontmatter says for a field, for the settings panel."""
    out = {}
    try:
        agents = job.repo_root() / ".claude" / "agents"
    except job.JobError:
        return out
    for name in job.AGENTS:
        path = agents / f"{name}.md"
        if path.exists():
            m = re.search(rf"^{field}:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
            out[name] = m.group(1) if m else absent
    return out


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
        if not isinstance(title, str) or not title.strip() or "\n" in title.strip() or len(title) > 300:
            raise job.JobError("each item needs a one-line title of at most 300 characters")
        if not isinstance(details, str) or len(details) > 20_000:
            raise job.JobError("details must be text of at most 20,000 characters")
        lines = [f"- [ ] {title.strip()}"]
        # Details sit under the item, indented, so the backlog stays one Markdown list.
        lines += [f"  {l}".rstrip() if l.strip() else "" for l in details.strip("\n").splitlines()]
        out.append({"open": True, "lines": lines})
    return out


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
                return self.send_json(200, {
                    "config": job.load_config(),
                    "agentDefaults": agent_defaults("model", "inherit"),
                    "agentEfforts": agent_defaults("effort", "session"),
                    "models": sorted(job.MODELS - {"default"}),
                    "efforts": [e for e in job.EFFORTS if e != "default"],
                    "agents": list(job.AGENTS),
                })
            except job.JobError as err:
                return self.send_json(500, {"error": str(err)})
        if self.path.startswith("/api/"):
            return self.send_json(404, {"error": "no such endpoint"})
        super().do_GET()

    def change(self):
        if not self.host_ok() or self.headers.get("X-Factory") != "1":
            return self.send_json(403, {"error": "changes need the X-Factory header from this server's page"})
        path = self.path.split("?")[0]
        try:
            body = self.read_body()
            if path == "/api/config" and self.command == "PUT":
                return self.send_json(200, {"config": job.save_config(body)})
            m = re.fullmatch(r"/api/jobs/([^/]+)/(merge|delete)", path)
            if m and self.command == "POST":
                job_id, action = m.groups()
                if action == "merge":
                    return self.send_json(200, {"message": job.merge(job_id)})
                return self.send_json(200, {"message": job.delete(job_id, bool(body.get("force")))})
            if path == "/api/backlog" and self.command == "POST":
                edit_backlog(body)
                return self.send_json(200, {"ok": True})
            return self.send_json(404, {"error": "no such endpoint"})
        except job.Busy as err:
            return self.send_json(409, {"error": str(err), "busy": True})
        except job.JobError as err:
            return self.send_json(400, {"error": str(err)})

    do_POST = change
    do_PUT = change


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
