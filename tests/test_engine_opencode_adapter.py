"""Recorded OpenCode streams plus synthetic coverage of plan §6.5/§13.4 rows."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from agent_factory import run_cli
from agent_factory.engine import gitops, pipeline
from agent_factory.engine.adapters import get_adapter
from agent_factory.engine.adapters.base import Prompt, UnsupportedProviderError, mask_secrets
from agent_factory.engine.adapters.opencode import OpencodeAdapter
from agent_factory.engine.config import MachineSettings
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import Supervisor
from engine_harness import create_repo

FIXTURES = Path(__file__).parent / "fixtures/opencode"
MODEL = "opencode/fake-model"


def encoded(event):
    return (json.dumps(event) + "\n").encode()


def tool(kind, **fields):
    return {"type": "tool_use", "part": {"tool": kind, "state": {"input": fields}}}


def error(message, kind="provider.no-route"):
    return {"type": "error", "error": {"type": kind, "message": message}}


def packet(context):
    return Prompt("Stable preamble and role.\n",
                  "Continue. Write " + str(context.directory / "report.md"),
                  {"report_path": str(context.directory / "report.md")})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifier = create_repo(tmp_path / "repo č", provider="opencode", model=MODEL)
    scheduler = Scheduler()
    root, writer = scheduler.repos[identifier]
    worktree = root / "worktree-1"
    for args in [("init", "-q", "-b", "factory/001-job"), ("config", "user.name", "test"),
                 ("config", "user.email", "test@localhost"),
                 ("commit", "-q", "--allow-empty", "-m", "base")]:
        gitops.checked(*args, cwd=worktree)
    current = gitops.head(worktree)
    writer.call(lambda connection: connection.execute("UPDATE jobs SET base_head=?", (current, )))
    writer.call(lambda connection: connection.execute("UPDATE executions SET head_before=?",
                                                      (current, )))
    adapter = OpencodeAdapter(prompt_builder=packet)
    supervisor = Supervisor(scheduler, {"opencode": adapter})
    supervisor.start()
    context = scheduler.claim_next()
    context.directory.mkdir(parents=True)
    launched = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    scheduler.repos[identifier][1].call(lambda connection: connection.execute(
        "UPDATE attempts SET t_launch=? WHERE id=?", (launched, context.attempt["id"])))
    context = scheduler.context(identifier, context.attempt["id"])
    yield scheduler, supervisor, context
    scheduler.close()


@pytest.mark.parametrize("capture", sorted(p.stem for p in FIXTURES.glob("*.jsonl")))
def test_every_recorded_fixture_parses(capture):
    adapter = OpencodeAdapter()
    events = [
        json.loads(line) for line in (FIXTURES / (capture + ".jsonl")).read_bytes().splitlines()
    ]
    updates = [adapter.parse_event(encoded(event)) for event in events]
    updates += [
        adapter.parse_event(line)
        for line in (FIXTURES / (capture + ".stderr")).read_bytes().splitlines()
    ]
    # Every recorded line carries the session id, so resuming never re-derives it.
    sessions = [e["sessionID"] for e in events if e.get("sessionID")]
    assert [u.session_id for u in updates if u.session_id] == sessions
    steps = [e for e in events if e["type"] == "step_finish"]
    usage = [u.usage for u in updates if u.usage is not None]
    assert [u["raw"][0] for u in usage] == [step["part"]["tokens"] for step in steps]
    for step, normalized in zip(steps, usage):
        tokens, cost = step["part"]["tokens"], step["part"]["cost"]
        assert normalized == {
            "input": tokens["input"],
            "cached_input": tokens["cache"]["read"],
            "output": tokens["output"],
            "cost_usd": cost,
            "raw": [tokens]
        }
    # Replaying the same bytes from a fresh adapter instance is deterministic.
    assert [OpencodeAdapter().parse_event(encoded(event))
            for event in events] == updates[:len(events)]
    activities = [u.activity for u in updates if u.activity]
    if capture == "tool":
        assert "reading notes.txt" in activities
    if capture == "shell":
        assert activities == ["running `git status --porcelain`"]
    if capture == "write":
        assert any(activity.startswith("editing ") for activity in activities)
    if capture == "deny":
        assert any(u.permission_denials for u in updates)
        assert any("Permission denied: shell" in denial for u in updates
                   for denial in u.permission_denials)
    if capture in {"error-model", "error-variant"}:
        assert any(u.errors for u in updates)
    if capture in {"resume", "text"}:
        assert activities == []


@pytest.mark.parametrize("line", [
    b"", b"not json", b"{", b"null", b"[]", b'"text"', b"\xff", b'{"part":null}', b'{"type":[]}',
    b'{"type":"tool_use","part":{"state":{"input":[]}}}'
])
def test_parser_tolerates_noise_and_partial_or_unknown_events(line):
    assert OpencodeAdapter().parse_event(line).session_id is None


def test_session_and_usage_normalization_only_from_real_events():
    adapter = OpencodeAdapter()
    assert adapter.parse_event(encoded({"type": "other"})).session_id is None
    assert adapter.parse_event(encoded({
        "type": "text",
        "sessionID": "ses_recorded"
    })).session_id == "ses_recorded"
    update = adapter.parse_event(
        encoded({
            "type": "step_finish",
            "part": {
                "tokens": {
                    "input": 0,
                    "output": True,
                    "cache": {
                        "read": "x"
                    }
                },
                "cost": "free"
            }
        }))
    assert update.usage == {
        "input": 0,
        "cached_input": None,
        "output": None,
        "cost_usd": None,
        "raw": [{
            "input": 0,
            "output": True,
            "cache": {
                "read": "x"
            }
        }]
    }
    assert adapter.parse_event(encoded({"type": "step_finish"})).usage is None
    assert adapter.parse_event(encoded({"type": "turn.completed"})).usage is None


def test_plain_text_diagnostics_only_when_they_match():
    adapter = OpencodeAdapter()
    assert adapter.parse_event(b"Error: 401 Unauthorized\n").errors
    assert adapter.parse_event(b"just noise\n").errors == ()
    denied = adapter.parse_event(b"shell tool rejected: Permission denied\n")
    assert denied.errors and denied.permission_denials


@pytest.mark.parametrize("kind", ["fresh", "contract_retry", "resume"])
def test_command_and_prompt_use_machine_path_and_exact_session(store, kind):
    _, _, context = store
    context.attempt.update(kind=kind, session_id="exact-session" if kind == "resume" else None)
    executable = str(context.root / "tools č/opencode.exe")
    adapter = OpencodeAdapter(MachineSettings(cli={"opencode": executable}), prompt_builder=packet)
    if kind == "contract_retry":
        context = replace(context,
                          previous={
                              "state": "invalid",
                              "error_detail": "invalid first line"
                          })
    prepared = adapter.prepare(context)
    argv = prepared.command["argv"]
    assert argv[:8] == [
        executable, "run", "--format", "json", "--standalone", "--auto", "--model", MODEL + "#low"
    ]
    assert argv[8:] == (["--session", "exact-session"] if kind == "resume" else [])
    assert prepared.command["cwd"] == context.job["worktree"]
    assert prepared.command["env"] == {"PYTHONUTF8": "1"}
    assert prepared.command["stdin"] == "prompt.md"
    assert prepared.system == "Stable preamble and role.\n"
    assert prepared.prompt.startswith("Continue." if kind == "resume" else prepared.system + "\n\n")
    assert str(context.directory / "report.md") in prepared.prompt
    assert prepared.packet_bytes == len(packet(context).text.encode("utf-8"))
    # The frozen "auto" permission policy maps to `--auto` only; never a positional prompt.
    assert "--approve-for-me" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert not any(arg.startswith("Reply") for arg in argv)
    if kind == "contract_retry":
        assert prepared.packet["validation_error"] == "invalid first line"
        assert "invalid first line" in prepared.prompt


def test_model_variant_path_fallback_missing_session_and_lost_work_warning(store, monkeypatch):
    _, _, context = store
    adapter = OpencodeAdapter(prompt_builder=packet)
    assert adapter.command(context)["argv"][0] == (shutil.which("opencode") or "opencode")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert adapter.command(context)["argv"][0] == "opencode"
    context.execution["model"] = MODEL + "#high"
    assert adapter.command(context)["argv"][-1] == MODEL + "#high"
    context.execution["model"] = MODEL
    context = replace(context, previous={"state": "lost"})
    assert "partial uncommitted work" in adapter.prepare(context).prompt
    assert adapter.prepare(context).packet["retry_context"]
    context.attempt.update(kind="resume", session_id=None)
    with pytest.raises(ValueError, match="exact sessionID"):
        adapter.command(context)
    assert callable(OpencodeAdapter().prompt_builder)


@pytest.mark.parametrize("provider", ["claude", "unknown", ""])
def test_unavailable_provider_is_explicit_and_opencode_loads_independently(provider):
    assert isinstance(get_adapter("opencode"), OpencodeAdapter)
    with pytest.raises(UnsupportedProviderError, match="Unsupported provider"):
        get_adapter(provider)


# Every §6.5 row, including the two invalid-report rows, is applied by the real
# supervisor so retry, pause reason and pipeline behavior are checked as well.
@pytest.mark.parametrize("exit_code,report,kind,events,reason,expected", [
    (0, "APPROVE\n", "fresh", [], None, ("valid", None, "needs_human", "verify")),
    (0, None, "fresh", [], None, ("invalid", "contract", "running", None)),
    (0, "invalid", "fresh", [], None, ("invalid", "contract", "running", None)),
    (0, "invalid", "contract_retry", [], None, ("invalid", "contract", "needs_human", "contract")),
    (1, None, "fresh", [error("401 Unauthorized")], None, ("failed", "auth", "paused", "auth")),
    (1, None, "fresh", [error("permission denied; shell: npm test --token=private")], None,
     ("failed", "permission", "paused", "permission")),
    (1, None, "fresh", [error("max budget exceeded")], None,
     ("failed", "budget", "paused", "budget")),
    (1, None, "fresh", [error("429 rate limit after retries")], None,
     ("failed", "rate_limit", "paused", "rate_limit")),
    (1, None, "fresh", [error("server overloaded")], None,
     ("failed", "rate_limit", "paused", "rate_limit")),
    (1, None, "fresh", [], "timeout", ("stopped", "timeout", "paused", "timeout")),
    (17, "APPROVE\n", "fresh", [], None, ("failed", "crash", "paused", "process")),
])
def test_classification_table_applies_to_job(store, exit_code, report, kind, events, reason,
                                             expected):
    scheduler, supervisor, context = store
    writer = scheduler.repos[context.repo_id][1]
    writer.call(lambda connection: connection.execute("UPDATE attempts SET kind=? WHERE id=?",
                                                      (kind, context.attempt["id"])))
    if report is not None:
        (context.directory / "report.md").write_text(report, encoding="utf-8")
    (context.directory / "events.jsonl").write_bytes(b"".join(encoded(e) for e in events))
    info = {"exit_code": exit_code, "ended_at": datetime.now(timezone.utc).isoformat()}
    if reason:
        info["reason"] = reason
    run_cli.atomic_json(context.directory / "exit.json", info)
    supervisor.finalise(scheduler.context(context.repo_id, context.attempt["id"]))
    updated = scheduler.context(context.repo_id, context.attempt["id"])
    assert (updated.attempt["state"], updated.attempt["error_class"], updated.job["state"],
            updated.job["pause_reason"]) == expected
    assert "private" not in (updated.attempt["error_detail"] or "")
    if updated.attempt["state"] == "invalid":
        assert updated.execution["state"] == ("pending" if kind != "contract_retry" else "failed")
        if kind != "contract_retry":
            retry = scheduler.claim_next()
            assert retry.attempt["kind"] == "contract_retry" and retry.attempt["session_id"] is None


@pytest.mark.parametrize("capture", sorted(p.stem for p in FIXTURES.glob("*.jsonl")))
def test_recorded_exit_classification(store, capture):
    _, supervisor, context = store
    meta = json.loads((FIXTURES / (capture + ".meta.json")).read_text(encoding="utf-8"))
    (context.directory /
     "events.jsonl").write_bytes((FIXTURES / (capture + ".jsonl")).read_bytes() +
                                 (FIXTURES / (capture + ".stderr")).read_bytes())
    if capture != "deny":
        (context.directory / "report.md").write_text("APPROVE\n", encoding="utf-8")
    outcome = supervisor.adapters["opencode"].classify(context, {"exit_code": meta["exit_code"]})
    if meta["exit_code"]:
        assert (outcome.state, outcome.error_class) == ("failed", "crash")
    elif capture == "deny":
        # A denied tool still exits zero, so the missing report is what pauses.
        assert (outcome.state, outcome.error_class) == ("invalid", "contract")
        assert outcome.detail and mask_secrets(outcome.detail) == outcome.detail
    else:
        assert (outcome.state, outcome.error_class) == ("valid", None)


def test_classify_uses_last_error_line_and_error_class(store):
    _, supervisor, context = store
    (context.directory / "events.jsonl").write_bytes(
        encoded(error("401 Unauthorized first")) + encoded(error("429 rate limit last")))
    outcome = supervisor.adapters["opencode"].classify(context, {"exit_code": 1})
    assert (outcome.state, outcome.error_class) == ("failed", "rate_limit")
    assert outcome.detail == "429 rate limit last"


def test_zero_exit_validates_even_with_stale_error_lines(store):
    _, supervisor, context = store
    (context.directory / "events.jsonl").write_bytes(encoded(
        error("401 Unauthorized reconnecting")))
    (context.directory / "report.md").write_text("APPROVE\n", encoding="utf-8")
    assert supervisor.adapters["opencode"].classify(context, {"exit_code": 0}).state == "valid"


@pytest.mark.parametrize("event,expected", [
    (tool("read", path="/repo/src/theme.ts"), "reading src/theme.ts"),
    (tool("read", path="src/theme.ts"), "reading src/theme.ts"),
    (tool("write", path="/repo/src/theme.ts"), "editing src/theme.ts"),
    (tool("edit", filePath="/repo/src/theme.ts"), "editing src/theme.ts"),
    (tool("patch", path="/repo/report.md"), "writing report"),
    (tool("write", path="/repo/report.md"), "writing report"),
    (tool("write"), "editing"),
    (tool("shell", command="npm test\nsecond command"), "running `npm test`"),
    (tool("bash", command="echo APPROVE > /repo/report.md"), "writing report"),
    (tool("shell", command="npm test " + "arg " * 30), "running `" +
     ("npm test " + "arg " * 30)[:79] + "…`"),
    (tool("shell", command='git commit -m "Fix theme"'), 'committing "Fix theme"'),
    (tool("glob", pattern="TODO"), 'searching "TODO"'),
    (tool("grep", pattern="TODO"), 'searching "TODO"'),
    (tool("webfetch",
          url="http://127.0.0.1:5172/settings"), "opening http://127.0.0.1:5172/settings"),
    (tool("mystery"), "using mystery"),
    (tool("mystery", path="ignored"), "using mystery"),
    ({
        "type": "text",
        "part": {
            "type": "text"
        }
    }, "thinking"),
    ({
        "type": "step_start",
        "part": {}
    }, None),
    ({
        "type": "step_finish",
        "part": {
            "tokens": {
                "input": 1
            }
        }
    }, None),
])
def test_summary_table(event, expected):
    assert OpencodeAdapter().summarize(event,
                                       worktree=Path("/repo"),
                                       report_path=Path("/repo/report.md"),
                                       quiet_seconds=20) == expected


def test_thinking_requires_quiet_and_activity_is_replay_safe():
    adapter = OpencodeAdapter()
    text = {"type": "text", "part": {"type": "text"}}
    assert adapter.summarize(text, quiet_seconds=19.99) is None
    started = tool("shell", command="npm test")
    assert adapter.parse_event(encoded(started)).activity == "running `npm test`"
    assert adapter.parse_event(encoded({"type": "step_start", "part": {}})).activity is None
    assert adapter.parse_event(encoded(started)) == adapter.parse_event(encoded(started))


def test_windows_relative_paths_and_report_destination():
    adapter = OpencodeAdapter()
    root = Path("C:/Work Tree")
    assert adapter.summarize(tool("read", path="c:\\work tree\\src\\theme.ts"),
                             worktree=root) == "reading src/theme.ts"
    assert adapter.summarize(tool("write", path="c:\\work tree\\report.md"),
                             worktree=root,
                             report_path=Path("C:/Work Tree/report.md")) == "writing report"


@pytest.mark.parametrize("secret", [
    "--token=private", "--token private", '--api-key="two words"', "KEY=private", "API_KEY=private",
    "password='two words'", '"api_key": "private"', "Bearer private", "sk-privatekey",
    "bXlzZWNyZXQ" * 6
])
def test_secret_masking_across_activity_and_diagnostics(secret):
    safe = mask_secrets(secret)
    assert "…" in safe and "private" not in safe and "two words" not in safe
    assert "bXlzZWNyZXQ" not in safe
    adapter = OpencodeAdapter()
    summary = adapter.summarize(tool("shell", command="npm test " + secret))
    assert "…" in summary and "private" not in summary
    update = adapter.parse_event(encoded(error("permission denied " + secret)))
    assert "private" not in " ".join(update.errors + update.permission_denials)


def test_usage_accumulates_across_offsets_and_recovery_and_activity_stays_replay_safe(store):
    scheduler, supervisor, context = store
    path = context.directory / "events.jsonl"
    turn = {
        "type": "step_finish",
        "sessionID": "recorded-session",
        "part": {
            "tokens": {
                "input": 10,
                "output": 2,
                "cache": {
                    "read": 4,
                    "write": 0
                }
            },
            "cost": 0.01
        }
    }
    path.write_bytes(
        encoded({
            "type": "step_start",
            "sessionID": "recorded-session"
        }) + encoded(tool("shell", command="npm test")) + encoded(turn))
    supervisor.tail(context)
    context = scheduler.context(context.repo_id, context.attempt["id"])
    assert context.attempt["session_id"] == "recorded-session"
    with path.open("ab") as stream:
        stream.write(encoded(turn))
    # New adapter instance, as after restart; totals come from the durable offset.
    replacement = Supervisor(scheduler, {"opencode": OpencodeAdapter()})
    replacement.tail(context)
    replacement.tail(context)  # same offset cannot double-count
    context = scheduler.context(context.repo_id, context.attempt["id"])
    usage = json.loads(context.attempt["usage"])
    assert usage == {
        "input": 20,
        "cached_input": 8,
        "output": 4,
        "cost_usd": 0.02,
        "raw": [turn["part"]["tokens"], turn["part"]["tokens"]]
    }
    assert context.attempt["last_activity"] == "running `npm test`"


def test_assistant_only_run_can_show_thinking_after_twenty_seconds(store):
    scheduler, supervisor, context = store
    context.attempt["t_launch"] = (datetime.now(timezone.utc) - timedelta(seconds=21)).isoformat()
    (context.directory / "events.jsonl").write_bytes(
        encoded({
            "type": "text",
            "sessionID": "ses_text",
            "part": {
                "type": "text",
                "text": "Considering the task"
            }
        }))
    supervisor.tail(context)
    context = scheduler.context(context.repo_id, context.attempt["id"])
    assert context.attempt["last_activity"] == "thinking"


def test_real_git_head_is_returned_and_unavailable_git_evidence_pauses(store, monkeypatch):
    _, _, context = store
    worktree = Path(context.job["worktree"])
    head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                                   text=True).strip()
    (context.directory / "report.md").write_text("APPROVE\n", encoding="utf-8")
    outcome = OpencodeAdapter().classify(context, {"exit_code": 0})
    assert outcome.completion.head_after == outcome.current_head == head
    assert outcome.completion.report_path == context.attempt["dir"] + "/report.md"

    def unavailable(*args):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(gitops, "validate_result", unavailable)
    outcome = OpencodeAdapter().classify(context, {"exit_code": 0})
    assert (outcome.state, outcome.error_class) == ("failed", "crash")


def test_default_packet_is_written_and_its_bytes_recorded_with_fake_cli(store):
    scheduler, supervisor, context = store
    folder = context.root / "factory/jobs" / context.job["id"]
    (folder / "request.md").write_text("Approve the committed feature.", encoding="utf-8")
    run_cli.atomic_json(context.directory / "scenario.json", {
        "report": "APPROVE\n",
        "report_path": str(context.directory / "report.md"),
        "sleep": 0.1
    })
    fake = Path(__file__).with_name("fake_cli.py").resolve()
    adapter = OpencodeAdapter()
    adapter.command = lambda claim: {
        "argv": [
            sys._base_executable, "-I", "-S",
            str(fake), "--scenario",
            str(context.directory / "scenario.json")
        ],
        "cwd":
        context.job["worktree"],
        "stdin":
        "prompt.md",
        "env": {}
    }
    supervisor.adapters["opencode"] = adapter
    supervisor.launch(context)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        supervisor.poll()
        updated = scheduler.context(context.repo_id, context.attempt["id"])
        if updated.attempt["state"] == "valid":
            break
        time.sleep(0.02)
    else:
        raise AssertionError(updated.attempt)
    from agent_factory.engine.packet import render
    packet_data = json.loads((context.directory / "packet.json").read_bytes())
    assert updated.attempt["packet_bytes"] == len(render(packet_data).encode("utf-8"))
    system = (context.directory / "system.md").read_bytes()
    assert (context.directory / "prompt.md").read_bytes().startswith(system + b"\n\n")
    assert updated.job["state"] == "needs_human" and updated.job["pause_reason"] == "verify"
    for child in supervisor._children.values():
        child.wait(timeout=15)
