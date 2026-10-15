"""Run the CLI orchestrator plan's Codex sessions one after another.

  python tools/run_plan.py               run the next phase, review it with Astra, fix, stop
  python tools/run_plan.py --dry-run     show what would run
  python tools/run_plan.py --status      list sessions and whether each is done
  python tools/run_plan.py --phase 2     run a specific phase (skips sessions already done)
  python tools/run_plan.py --no-pause    continue into the following phases without stopping
  python tools/run_plan.py --brief       one line per command/edit/message (default: full detail)
  python tools/run_plan.py --quiet       hide live Codex activity

Live view (default): Codex's full messages, reasoning summaries, each command with the tail of its
output, a git diff for every file it edits, its step plan, and token usage per turn.

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
if not (REPO / "docs" / "cli-orchestrator-prompts.md").exists():   # e.g. run from a copy elsewhere
    REPO = Path.cwd()
PROMPTS = REPO / "docs" / "cli-orchestrator-prompts.md"
LOGS = REPO / ".git" / "plan-run"
MODELS = {"Sol": "gpt-6.1-sol", "Astra": "gpt-6-astra", "Luna": "gpt-6-luna"}
EFFORTS = {"Light": "low", "Medium": "medium", "High": "high", "Extra High": "xhigh"}
HEADING = re.compile(r"^### (\d+)\.(\d+) (.+?) · (you \+ )?(Sol|Luna|Astra) · (Light|Medium|High|Extra High)(.*)$")
RESULT = re.compile(r"SESSION-RESULT: (PASS|FAIL)(.*)")
DETAIL = "full"           # full | brief | quiet (set from the command line)
REASONING = "detailed"    # Codex reasoning summaries: detailed | concise | auto | none


def sessions():
    found = []
    for line in PROMPTS.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if m:
            phase, n, title, manual, model, effort, rest = m.groups()
            found.append({"id": f"{phase}.{n}", "phase": int(phase), "title": title.strip(),
                          "manual": bool(manual), "model": MODELS[model], "effort": EFFORTS[effort],
                          "astra_fallback": "Astra" in rest,
                          "requires_claude": "requires Claude" in rest})
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


DIM, BOLD, RED, GREEN, CYAN, YELLOW, RESET = ("\033[2m", "\033[1m", "\033[31m", "\033[32m",
                                              "\033[36m", "\033[33m", "\033[0m")
SHELL_WRAPPER = re.compile(r'^"?[^"]*?(?:pwsh|powershell|bash|cmd)(?:\.exe)?"?\s+(?:-Command|-c|/c)\s+', re.I)
DIFF_LINES = 80
OUTPUT_LINES = 6


def command_text(cmd):
    """The command as the agent wrote it, without the shell wrapper Codex adds on Windows."""
    cmd = SHELL_WRAPPER.sub("", cmd or "").strip()
    if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in "'\"":
        cmd = cmd[1:-1]
    return cmd.replace("'\"'\"'", "'").replace("'\"'", "")


def file_diff(path):
    """A coloured git diff of one file against HEAD (new files shown in full), trimmed."""
    try:
        rel = Path(path).resolve().relative_to(REPO.resolve())
    except ValueError:
        return f"{DIM}      (outside the repo){RESET}"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)], cwd=REPO,
                             capture_output=True).returncode == 0
    null = "NUL" if sys.platform == "win32" else "/dev/null"
    args = (["git", "diff", "--no-color", "--", str(rel)] if tracked
            else ["git", "diff", "--no-color", "--no-index", "--", null, str(rel)])
    out = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    lines = [l for l in out.splitlines() if not l.startswith(("diff --git", "index ", "--- ", "+++ ", "new file mode"))]
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [f"… {len(lines) - DIFF_LINES} more diff lines (git diff -- {rel})"]
    colours = {"+": GREEN, "-": RED, "@": CYAN}
    return "\n".join(f"      {colours.get(l[:1], '')}{l}{RESET}" for l in lines) or f"{DIM}      (no diff against HEAD){RESET}"


def report(line):
    """Print a Codex JSON event: full messages, reasoning, commands with output, diffs, plan, usage."""
    try:
        event = json.loads(line)
    except ValueError:
        if line.strip():
            print(f"    {short(line)}", flush=True)
        return
    kind = event.get("type", "")
    item = event.get("item") or {}
    itype = item.get("type") or item.get("item_type") or ""
    t = time.strftime("%H:%M:%S")
    brief = DETAIL == "brief"
    if kind == "thread.started":
        print(f"    {t} session {event.get('thread_id', '?')}  (codex resume <id> to open it)")
    elif kind == "item.started" and itype == "command_execution":
        print(f"    {t} {YELLOW}${RESET} {short(command_text(item.get('command')), 100) if brief else command_text(item.get('command'))}")
    elif kind == "item.completed" and itype == "command_execution":
        code = item.get("exit_code")
        if brief:
            if code not in (None, 0):
                print(f"    {t}   {RED}✗ exit {code}{RESET}")
        else:
            out = (item.get("aggregated_output") or "").rstrip().splitlines()
            mark = f"{GREEN}ok{RESET}" if code == 0 else f"{RED}exit {code}{RESET}"
            print(f"             {mark}" + (f"  {DIM}({len(out)} lines){RESET}" if out else ""))
            for l in out[-OUTPUT_LINES:] if code == 0 else out[-OUTPUT_LINES * 3:]:
                print(f"             {DIM}│ {l[:160]}{RESET}")
    elif kind == "item.completed" and itype == "file_change":
        for change in item.get("changes", []):
            print(f"    {t} {CYAN}✎ {change.get('kind', 'edit')} {change.get('path')}{RESET}")
            if not brief:
                print(file_diff(change.get("path", "")))
    elif kind == "item.completed" and itype == "agent_message":
        text = item.get("text", "").strip()
        print(f"    {t} {BOLD}»{RESET} {short(text)}" if brief else f"\n    {t} {BOLD}Codex:{RESET} {text}\n")
    elif kind == "item.completed" and itype == "reasoning":
        if not brief:
            thought = " · ".join(p.strip() for p in item.get("text", "").replace("**", "\n").splitlines() if p.strip())
            print(f"    {t} {DIM}thinking: {thought}{RESET}")
    elif kind == "item.completed" and itype == "todo_list":
        todos = [x for x in item.get("items", []) if isinstance(x, dict)]
        if brief:
            print(f"    {t} ☐ plan: {sum(1 for x in todos if x.get('completed'))}/{len(todos)} steps done")
        else:
            for x in todos:
                print(f"             {'☑' if x.get('completed') else '☐'} {x.get('text', '')}")
    elif kind == "item.completed" and itype in ("mcp_tool_call", "web_search"):
        print(f"    {t} · {itype}: {short(item.get('query') or item.get('tool') or '', 90)}")
    elif kind == "item.completed" and itype == "error":
        print(f"    {t} {RED}! {item.get('message', '')}{RESET}")
    elif kind == "turn.completed":
        u = event.get("usage") or {}
        print(f"    {t} {DIM}tokens: in {u.get('input_tokens', '?')} (cached {u.get('cached_input_tokens', '?')}), "
              f"out {u.get('output_tokens', '?')}{RESET}")
    elif kind in ("turn.failed", "error"):
        err = event.get("error") or {}
        print(f"    {t} {RED}! {err.get('message') if isinstance(err, dict) else err or event.get('message', kind)}{RESET}")
    sys.stdout.flush()


def run_codex(label, prompt, model, effort, permissions, timeout, review_report=None):
    codex = shutil.which("codex") or sys.exit("codex isn't on PATH")
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log, last = LOGS / f"{label}-{stamp}.jsonl", LOGS / f"{label}-{stamp}.last.md"
    perm = (["--dangerously-bypass-approvals-and-sandbox"] if permissions == "bypass"
            else ["--approve-for-me"])
    # Reasoning summaries reach `exec --json` only with these set (checked with codex-cli 0.162.1).
    reasoning = (["-c", f'model_reasoning_summary="{REASONING}"', "-c", "hide_agent_reasoning=false",
                  "-c", "show_raw_agent_reasoning=true"] if REASONING != "none" else [])
    args = [codex, "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"', *reasoning,
            *perm, "-C", str(REPO)]
    # A custom review prompt carries the exact range; --base and a prompt are mutually exclusive.
    args += (["review"] if review_report is not None else []) + ["--json", "-o", str(last), "-"]
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
                if DETAIL != "quiet":
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
    if review_report is not None:
        if not final.strip():
            return False, "Codex review returned no findings report"
        review_report.parent.mkdir(parents=True, exist_ok=True)
        review_report.write_text(final, encoding="utf-8")
        return True, ""
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


def review_marker(phase, sessions_in_phase):
    if any(s["id"] == "1.11" for s in sessions_in_phase):
        return f"[phase {phase} Claude parity review fixes]"
    return f"[phase {phase} review fixes]"


def review_and_fix(phase, sessions_in_phase, permissions, timeout, reviewer="codex"):
    """Review the phase with a separate model, then let Sol fix verified findings."""
    if reviewer == "none":
        return True
    LOGS.mkdir(parents=True, exist_ok=True)
    claude_parity = any(s["id"] == "1.11" for s in sessions_in_phase)
    marker = review_marker(phase, sessions_in_phase)
    if git("log", "--oneline", "--fixed-strings", f"--grep={marker}"):
        return True
    first = sessions_in_phase[0]["id"]
    commits = git("log", "--reverse", "--format=%H", "--fixed-strings", f"--grep=[session {first}]").splitlines()
    if not commits:
        return True
    base = f"{commits[0]}~1"
    suffix = "-claude-parity" if claude_parity else ""
    report = REPO / "docs" / "reviews" / f"phase-{phase}{suffix}.md"
    if not report.exists():
        report.parent.mkdir(parents=True, exist_ok=True)
        ids = ", ".join(s["id"] for s in sessions_in_phase)
        prompt = (
            f"Review the diff `git diff {base}..HEAD` in this repo. It implements Phase {phase} of "
            f"docs/cli-orchestrator-plan.md through sessions {ids} of docs/cli-orchestrator-prompts.md. "
            "Review it against that spec, not just for generic bugs: every rule in the sections those "
            "sessions list, duplicate-launch / crash-recovery / process-ownership guarantees, Windows "
            "behaviour, and whether tests assert the spec's behaviour. Verify each finding before "
            "reporting it. Do not edit files or create commits. Return the findings, most severe "
            "first, each with file:line, the failure scenario and a suggested fix. "
            "Distinguish deferred Claude work (session 1.11) from actual regressions. "
            "If there are none, return 'No findings.'")
        effort = "xhigh" if phase == 1 else "high"
        if reviewer == "codex":
            ok, reason = run_codex(f"review-phase-{phase}", prompt, MODELS["Astra"], effort,
                                   permissions, timeout, review_report=report)
            if not ok:
                print(f"\nStopped: phase {phase} review failed: {reason}")
                return False
        else:
            claude = shutil.which("claude") or sys.exit("claude isn't on PATH")
            print(f"  → claude review (opus · {effort}) → {report.relative_to(REPO)}", flush=True)
            try:
                result = subprocess.run([claude, "-p", "--model", "opus", "--effort", effort,
                                         "--permission-mode", "auto"], cwd=REPO, input=prompt,
                                        capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=timeout * 60)
            except subprocess.TimeoutExpired:
                print(f"\nStopped: Claude review timed out after {timeout} min")
                return False
            (LOGS / f"review-phase-{phase}.log").write_text(
                result.stdout + result.stderr, encoding="utf-8")
            if result.returncode or not result.stdout.strip():
                print(f"\nStopped: Claude review failed (exit {result.returncode}); see {LOGS}")
                return False
            report.write_text(result.stdout, encoding="utf-8")
    prompt = (
        f"Read docs/cli-orchestrator-prompts.md and apply its \"Shared preamble\". Then read "
        f"{report.relative_to(REPO).as_posix()}: the review of Phase {phase}. Fix every finding "
        "you agree with, each with a test; for any you disagree with, explain why in the review file "
        f"under a '## Not fixed' heading. Commit everything (including the review file) with a message "
        f"starting with \"{marker}\" instead of a session number. End with SESSION-RESULT: PASS "
        "only after the relevant tests pass, the commit exists, and the working tree is clean.")
    ok, reason = run_codex(f"phase-{phase}-fixes", prompt, MODELS["Sol"], "high", permissions, timeout)
    if ok and not git("log", "--oneline", "--fixed-strings", f"--grep={marker}"):
        ok, reason = False, f"no commit starting with {marker}"
    if ok and not clean():
        ok, reason = False, "working tree not clean after review fixes"
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
    p.add_argument("--reviewer", choices=("claude", "codex", "none"), default="codex",
                   help="phase reviewer (default: codex with Astra; fixes use Sol)")
    p.add_argument("--no-review", action="store_true", help="alias for --reviewer none")
    p.add_argument("--include-claude", action="store_true",
                   help="include deferred Claude fixtures and adapter session (requires Claude usage)")
    p.add_argument("--permissions", choices=("auto", "bypass"), default="bypass",
                   help="bypass = no sandbox or approvals (default; sessions need network, installs "
                        "and nested claude/codex runs); auto = codex --approve-for-me")
    p.add_argument("--timeout", type=int, default=240, help="minutes per Codex session (default 240)")
    p.add_argument("--brief", action="store_true", help="one line per command/edit/message instead of full detail")
    p.add_argument("--quiet", action="store_true", help="don't show live Codex activity, only progress lines")
    p.add_argument("--reasoning", choices=("detailed", "concise", "auto", "none"), default="detailed",
                   help="ask Codex for reasoning summaries and show them (default detailed)")
    a = p.parse_args()
    if a.no_review:
        a.reviewer = "none"
    global DETAIL, REASONING
    DETAIL = "quiet" if a.quiet else "brief" if a.brief else "full"
    REASONING = a.reasoning
    if sys.platform == "win32":
        import os
        os.system("")  # enable ANSI colours in the Windows console

    all_sessions = sessions()
    if a.status:
        for s in all_sessions:
            print(f"{'✓' if done(s['id']) else '·'} {s['id']:5} {s['model']:12} {s['effort']:6} "
                  f"{'(manual) ' if s['manual'] else ''}{s['title']}"
                  f"{' (deferred; --include-claude)' if s['requires_claude'] and not a.include_claude else ''}")
        return 0
    all_sessions = [s for s in all_sessions if a.include_claude or not s["requires_claude"]]

    phases = sorted({s["phase"] for s in all_sessions})
    pending = [s["phase"] for s in all_sessions if not done(s["id"])]
    if a.reviewer != "none":
        # A failed/skipped review must be retried before moving to a later phase.
        pending += [phase for phase in phases if phase >= 1 and not git(
            "log", "--oneline", "--fixed-strings",
            f"--grep={review_marker(phase, [s for s in all_sessions if s['phase'] == phase])}")]
    start = a.phase if a.phase is not None else (min(pending) if pending else None)
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
        if phase >= 1 and a.reviewer != "none":
            print(f"▶ Phase {phase} review ({'Codex Astra' if a.reviewer == 'codex' else 'Claude'}) and fixes (Codex Sol)")
            if not a.dry_run and not review_and_fix(phase, todo, a.permissions, a.timeout, a.reviewer):
                return 1
        if not a.no_pause and not a.dry_run:
            suffix = "-claude-parity" if any(s["id"] == "1.11" for s in todo) else ""
            review_note = (f", and docs/reviews/phase-{phase}{suffix}.md"
                           if phase >= 1 and a.reviewer != "none" else "")
            print(f"\n⏸ Phase {phase} complete. Check `git log`, the exit criteria in plan §17, and "
                  f"the session test results{review_note}, then rerun to continue.")
            return 0
    print("\nNothing left to run." if not a.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
