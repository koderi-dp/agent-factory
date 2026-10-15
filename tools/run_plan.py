"""Run the CLI orchestrator plan's Codex sessions one after another.

  python tools/run_plan.py               run the next phase, review it with Claude, fix, stop
  python tools/run_plan.py --dry-run     show what would run
  python tools/run_plan.py --status      list sessions and whether each is done
  python tools/run_plan.py --phase 2     run a specific phase (skips sessions already done)
  python tools/run_plan.py --no-pause    continue into the following phases without stopping
  python tools/run_plan.py --quiet       hide live Codex activity (commands, edits, messages)

Sessions and their model/effort come from docs/cli-orchestrator-prompts.md headings. A session
is done when its "[session X.Y]" commit exists. Logs go to .git/plan-run/.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROMPTS = REPO / "docs" / "cli-orchestrator-prompts.md"
LOGS = REPO / ".git" / "plan-run"
MODELS = {"Sol": "gpt-6.1-sol", "Astra": "gpt-6-astra", "Luna": "gpt-6-luna"}
EFFORTS = {"Light": "low", "Medium": "medium", "High": "high", "Extra High": "xhigh"}
HEADING = re.compile(r"^### (\d+)\.(\d+) (.+?) · (you \+ )?(Sol|Luna|Astra) · (Light|Medium|High|Extra High)(.*)$")
RESULT = re.compile(r"SESSION-RESULT: (PASS|FAIL)(.*)")
VERBOSE = True


def sessions():
    found = []
    for line in PROMPTS.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if m:
            phase, n, title, manual, model, effort, rest = m.groups()
            found.append({"id": f"{phase}.{n}", "phase": int(phase), "title": title.strip(),
                          "manual": bool(manual), "model": MODELS[model], "effort": EFFORTS[effort],
                          "astra_fallback": "Astra" in rest})
    if not found:
        sys.exit(f"no sessions found in {PROMPTS}")
    return found


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def done(session_id):
    return bool(git("log", "--oneline", "--fixed-strings", f"--grep=[session {session_id}]"))


def clean():
    return git("status", "--porcelain") == ""


def short(text, limit=110):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def report(line):
    """Print one readable line for a Codex JSON event: commands, edits, messages, usage."""
    try:
        event = json.loads(line)
    except ValueError:
        if line.strip():
            print(f"    {short(line)}", flush=True)
        return
    kind = event.get("type", "")
    item = event.get("item") or {}
    itype = item.get("type") or item.get("item_type") or ""
    stamp = time.strftime("%H:%M:%S")
    text = None
    if kind == "thread.started":
        text = f"session {event.get('thread_id', '?')}  (codex resume <id> to open it)"
    elif kind == "item.started" and itype == "command_execution":
        text = f"$ {short(item.get('command', ''), 100)}"
    elif kind == "item.completed":
        if itype == "command_execution":
            code = item.get("exit_code")
            if code not in (None, 0):
                text = f"  ✗ exit {code}: {short(item.get('command', ''), 90)}"
        elif itype == "file_change":
            paths = [c.get("path", "?") for c in item.get("changes", []) if isinstance(c, dict)]
            text = f"✎ {short(', '.join(paths) or 'files', 100)}"
        elif itype == "agent_message":
            text = f"» {short(item.get('text', ''))}"
        elif itype == "todo_list":
            todos = item.get("items", [])
            finished = sum(1 for t in todos if isinstance(t, dict) and t.get("completed"))
            text = f"☐ plan: {finished}/{len(todos)} steps done"
        elif itype in ("mcp_tool_call", "web_search"):
            text = f"· {itype}: {short(item.get('query') or item.get('tool') or '', 90)}"
        elif itype == "error":
            text = f"! {short(item.get('message', ''))}"
    elif kind == "turn.completed":
        usage = event.get("usage") or {}
        text = (f"tokens: in {usage.get('input_tokens', '?')} (cached {usage.get('cached_input_tokens', '?')}), "
                f"out {usage.get('output_tokens', '?')}")
    elif kind in ("turn.failed", "error"):
        err = event.get("error") or {}
        text = f"! {short(err.get('message') if isinstance(err, dict) else err or event.get('message', kind))}"
    if text:
        print(f"    {stamp} {text}", flush=True)


def run_codex(label, prompt, model, effort, permissions, timeout):
    codex = shutil.which("codex") or sys.exit("codex isn't on PATH")
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log, last = LOGS / f"{label}-{stamp}.jsonl", LOGS / f"{label}-{stamp}.last.md"
    perm = (["--dangerously-bypass-approvals-and-sandbox"] if permissions == "bypass"
            else ["--approve-for-me"])
    args = [codex, "exec", "--json", "-m", model, "-c", f'model_reasoning_effort="{effort}"',
            *perm, "-C", str(REPO), "-o", str(last), "-"]
    print(f"  → codex {model} · {effort}   log: {log.relative_to(REPO)}", flush=True)
    process = subprocess.Popen(args, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    process.stdin.write(prompt)
    process.stdin.close()
    deadline = time.monotonic() + timeout * 60
    timer = threading.Timer(timeout * 60, process.kill)
    timer.start()
    try:
        with open(log, "w", encoding="utf-8") as out:
            for line in process.stdout:
                out.write(line)
                out.flush()
                if VERBOSE:
                    report(line)
        code = process.wait()
    finally:
        timer.cancel()
    if time.monotonic() >= deadline:
        return False, f"timed out after {timeout} min"
    final = last.read_text(encoding="utf-8") if last.exists() else ""
    m = None
    for m in RESULT.finditer(final):
        pass
    if code != 0:
        return False, f"codex exited {code}"
    if not m:
        return False, "no SESSION-RESULT line in the final message"
    if m.group(1) == "FAIL":
        return False, m.group(2).strip() or "session reported FAIL"
    return True, ""


def run_session(s, permissions, timeout):
    base = (f"Read docs/cli-orchestrator-prompts.md. Do session {s['id']}: follow the "
            f"\"Shared preamble\" and then the prompt for session {s['id']}.")
    attempts = [(s["model"], s["effort"]), (s["model"], s["effort"])]
    if s["astra_fallback"]:
        attempts.append((MODELS["Astra"], "high"))
    reason = ""
    for i, (model, effort) in enumerate(attempts, 1):
        prompt = base if i == 1 else (
            f"{base}\n\nA previous attempt at this session failed: {reason}\n"
            "The working tree may contain its uncommitted changes. Inspect them, keep what is "
            "correct, and finish the session.")
        ok, reason = run_codex(f"session-{s['id']}-a{i}", prompt, model, effort, permissions, timeout)
        if ok and not done(s["id"]):
            ok, reason = False, f"no commit starting with [session {s['id']}]"
        if ok and not clean():
            ok, reason = False, "working tree not clean after the session"
        if ok:
            return True
        print(f"  ✗ attempt {i}: {reason}", flush=True)
    print(f"\nStopped: session {s['id']} failed {len(attempts)} times. Last reason: {reason}\n"
          f"Logs: {LOGS}. Fix or adjust, then rerun.")
    return False


def review_and_fix(phase, sessions_in_phase, permissions, timeout):
    """Claude reviews the phase's commits, then a Codex session fixes the findings."""
    marker = f"[phase {phase} review fixes]"
    if git("log", "--oneline", "--fixed-strings", f"--grep={marker}"):
        return True
    first = sessions_in_phase[0]["id"]
    commits = git("log", "--reverse", "--format=%H", "--fixed-strings", f"--grep=[session {first}]").splitlines()
    if not commits:
        return True
    base = f"{commits[0]}~1"
    report = REPO / "docs" / "reviews" / f"phase-{phase}.md"
    if not report.exists():
        claude = shutil.which("claude") or sys.exit("claude isn't on PATH")
        report.parent.mkdir(parents=True, exist_ok=True)
        ids = ", ".join(s["id"] for s in sessions_in_phase)
        prompt = (
            f"Review the diff `git diff {base}..HEAD` in this repo. It implements Phase {phase} of "
            f"docs/cli-orchestrator-plan.md through sessions {ids} of docs/cli-orchestrator-prompts.md. "
            "Review it against that spec, not just for generic bugs: every rule in the sections those "
            "sessions list, duplicate-launch / crash-recovery / process-ownership guarantees, Windows "
            "behaviour, and whether tests assert the spec's behaviour. Verify each finding before "
            "reporting it. Do not change any code. Write the findings, most severe first, each with "
            f"file:line, the failure scenario and a suggested fix, to {report.relative_to(REPO).as_posix()}. "
            "If there are none, write 'No findings.'")
        effort = "xhigh" if phase == 1 else "high"
        print(f"  → claude review (opus · {effort}) → {report.relative_to(REPO)}", flush=True)
        with open(LOGS / f"review-phase-{phase}.log", "w", encoding="utf-8") as out:
            subprocess.run([claude, "-p", "--model", "opus", "--effort", effort,
                            "--permission-mode", "auto"], cwd=REPO, input=prompt, stdout=out,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8", timeout=timeout * 60)
        if not report.exists():
            print(f"\nStopped: Claude didn't write {report}. See {LOGS / f'review-phase-{phase}.log'}")
            return False
    prompt = (
        f"Read docs/cli-orchestrator-prompts.md and apply its \"Shared preamble\". Then read "
        f"{report.relative_to(REPO).as_posix()}: Claude's review of Phase {phase}. Fix every finding "
        "you agree with, each with a test; for any you disagree with, explain why in the review file "
        f"under a '## Not fixed' heading. Commit everything (including the review file) with a message "
        f"starting with \"{marker}\" instead of a session number.")
    ok, reason = run_codex(f"phase-{phase}-fixes", prompt, MODELS["Sol"], "high", permissions, timeout)
    if ok and not git("log", "--oneline", "--fixed-strings", f"--grep={marker}"):
        ok, reason = False, f"no commit starting with {marker}"
    if not ok:
        print(f"\nStopped: phase {phase} review fixes failed: {reason}")
    return ok


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase", type=int, help="run this phase (default: the first with unfinished sessions)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--no-pause", action="store_true", help="continue into the next phases")
    p.add_argument("--no-review", action="store_true", help="skip the Claude review + fix step")
    p.add_argument("--permissions", choices=("auto", "bypass"), default="auto",
                   help="auto = codex --approve-for-me (default); bypass = no sandbox, only in a throwaway VM")
    p.add_argument("--timeout", type=int, default=240, help="minutes per Codex session (default 240)")
    p.add_argument("--quiet", action="store_true", help="don't show live Codex activity, only progress lines")
    a = p.parse_args()
    global VERBOSE
    VERBOSE = not a.quiet

    all_sessions = sessions()
    if a.status:
        for s in all_sessions:
            print(f"{'✓' if done(s['id']) else '·'} {s['id']:5} {s['model']:12} {s['effort']:6} "
                  f"{'(manual) ' if s['manual'] else ''}{s['title']}")
        return 0

    phases = sorted({s["phase"] for s in all_sessions})
    pending = [s["phase"] for s in all_sessions if not done(s["id"])]
    start = a.phase if a.phase is not None else (pending[0] if pending else None)
    if start is None:
        print("All sessions are done.")
        return 0

    for phase in [ph for ph in phases if ph >= start]:
        todo = [s for s in all_sessions if s["phase"] == phase]
        print(f"\n=== Phase {phase} ===")
        for s in todo:
            if done(s["id"]):
                print(f"✓ {s['id']} {s['title']} (done)")
                continue
            if s["manual"]:
                print(f"\n⏸ Session {s['id']} needs you: {s['title']}. Run it yourself as described "
                      "in docs/cli-orchestrator-prompts.md, then rerun this script.")
                return 0
            print(f"▶ {s['id']} {s['title']}", flush=True)
            if a.dry_run:
                print(f"  (dry run) codex {s['model']} · {s['effort']}"
                      f"{' → Astra fallback' if s['astra_fallback'] else ''}")
                continue
            if not clean():
                print("\nStopped: the working tree has uncommitted changes. Commit or stash them first.")
                return 1
            if not run_session(s, a.permissions, a.timeout):
                return 1
            print(f"✓ {s['id']} done", flush=True)
        if phase >= 1 and not a.no_review:
            print(f"▶ Phase {phase} review (Claude) and fixes (Codex)")
            if not a.dry_run and not review_and_fix(phase, todo, a.permissions, a.timeout):
                return 1
        if not a.no_pause and not a.dry_run:
            print(f"\n⏸ Phase {phase} complete. Check `git log`, the exit criteria in plan §17, and "
                  f"docs/reviews/phase-{phase}.md, then rerun to continue.")
            return 0
    print("\nNothing left to run." if not a.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
