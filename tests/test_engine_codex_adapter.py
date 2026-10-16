"""Recorded Codex streams plus synthetic coverage of plan §6.5/§13.4 rows."""

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
from agent_factory.engine import gitops
from agent_factory.engine.adapters import get_adapter
from agent_factory.engine.adapters.base import Prompt, UnsupportedProviderError, mask_secrets
from agent_factory.engine.adapters.codex import CodexAdapter
from agent_factory.engine.config import MachineSettings
from agent_factory.engine.scheduler import Scheduler
from agent_factory.engine.supervisor import Supervisor
from engine_harness import create_repo

FIXTURES = Path(__file__).parent / "fixtures/codex"


def item(kind, **fields):
    return {"type": "item.started", "item": {"id": "item_0", "type": kind, **fields}}


def encoded(event):
    return (json.dumps(event) + "\n").encode()


def packet(context):
    return Prompt("Stable preamble and role.\n",
                  "Continue. Write " + str(context.directory / "report.md"),
                  {"report_path": str(context.directory / "report.md")})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "machine"))
    identifier = create_repo(tmp_path / "repo č")
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
    adapter = CodexAdapter(prompt_builder=packet)
    supervisor = Supervisor(scheduler, {"codex": adapter})
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
    adapter = CodexAdapter()
    events = [
        json.loads(line) for line in (FIXTURES / (capture + ".jsonl")).read_bytes().splitlines()
    ]
    updates = [adapter.parse_event(encoded(event)) for event in events]
    updates += [
        adapter.parse_event(line)
        for line in (FIXTURES / (capture + ".stderr")).read_bytes().splitlines()
    ]
    sessions = [e["thread_id"] for e in events if e["type"] == "thread.started"]
    assert [u.session_id for u in updates if u.session_id] == sessions
    usage = [e["usage"] for e in events if e["type"] == "turn.completed"]
    assert [u.usage["raw"][0] for u in updates if u.usage is not None] == usage
    for raw, normalized in zip(usage, [u.usage for u in updates if u.usage is not None]):
        assert normalized == {
            "input": raw["input_tokens"],
            "cached_input": raw["cached_input_tokens"],
            "output": raw["output_tokens"],
            "cost_usd": None,
            "raw": [raw]
        }
    if capture == "auth":
        assert any("401" in error for u in updates for error in u.errors)
    if capture == "denial-home":
        assert any(u.permission_denials for u in updates)
    if capture == "long":
        activities = [u.activity for u in updates if u.activity]
        assert len(activities) == 2 and activities[0] == activities[1]
        assert activities[0].startswith("running `Write-Output LONG_START")


@pytest.mark.parametrize("line", [
    b"", b"not json", b"{", b"null", b"[]", b'"text"', b"\xff", b'{"item":null}', b'{"type":[]}',
    b'{"type":"item.started","item":{"type":[]}}'
])
def test_parser_tolerates_noise_and_partial_or_unknown_events(line):
    assert CodexAdapter().parse_event(line).session_id is None


def test_unknown_usage_stays_null_and_only_thread_started_sets_session():
    adapter = CodexAdapter()
    assert adapter.parse_event(encoded({"type": "other", "session_id": "wrong"})).session_id is None
    update = adapter.parse_event(
        encoded({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 0,
                "output_tokens": True
            }
        }))
    assert update.usage == {
        "input": 0,
        "cached_input": None,
        "output": None,
        "cost_usd": None,
        "raw": [{
            "input_tokens": 0,
            "output_tokens": True
        }]
    }
    assert adapter.parse_event(encoded({"type": "turn.failed"})).usage is None


@pytest.mark.parametrize("permissions,flag",
                         [("auto", "--approve-for-me"),
                          ("bypass", "--dangerously-bypass-approvals-and-sandbox")])
@pytest.mark.parametrize("kind", ["fresh", "contract_retry", "resume"])
def test_command_and_prompt_use_frozen_profile_machine_path_and_exact_session(
        store, permissions, flag, kind):
    _, _, context = store
    context.job["frozen"]["permissions"] = permissions
    context.attempt.update(kind=kind, session_id="exact-thread" if kind == "resume" else None)
    executable = str(context.root / "tools č/codex.cmd")
    adapter = CodexAdapter(MachineSettings(cli={"codex": executable}), prompt_builder=packet)
    if kind == "contract_retry":
        context = replace(context,
                          previous={
                              "state": "invalid",
                              "error_detail": "invalid first line"
                          })
    prepared = adapter.prepare(context)
    argv = prepared.command["argv"]
    assert argv[:8] == [
        executable, "exec", "--json", "-m", "fake", "-c", 'model_reasoning_effort="low"', "-C"
    ]
    assert argv[8] == context.job["worktree"]
    assert argv[9:12] == ["--add-dir", str(context.root / "factory/jobs" / context.job["id"]), flag]
    assert argv[12:] == (["resume", "exact-thread", "-"] if kind == "resume" else ["-"])
    assert prepared.command["cwd"] == context.job["worktree"]
    assert prepared.command["env"] == {"PYTHONUTF8": "1"}
    assert prepared.command["stdin"] == "prompt.md"
    assert prepared.system == "Stable preamble and role.\n"
    assert prepared.prompt.startswith("Continue." if kind == "resume" else prepared.system + "\n\n")
    assert str(context.directory / "report.md") in prepared.prompt
    assert "developer_instructions" not in " ".join(argv)
    assert "--max-budget-usd" not in argv
    if kind == "contract_retry":
        assert prepared.packet["validation_error"] == "invalid first line"
        assert "invalid first line" in prepared.prompt


def test_path_fallback_missing_session_and_lost_work_warning(store):
    _, _, context = store
    adapter = CodexAdapter(prompt_builder=packet)
    assert adapter.command(context)["argv"][0] == (shutil.which("codex") or "codex")
    context = replace(context, previous={"state": "lost"})
    assert "partial uncommitted work" in adapter.prepare(context).prompt
    context.attempt.update(kind="resume", session_id=None)
    with pytest.raises(ValueError, match="exact thread.started"):
        adapter.command(context)
    assert callable(CodexAdapter().prompt_builder)


def test_resume_parent_flags_are_accepted_by_installed_cli(store):
    if not shutil.which("codex"):
        pytest.skip("Codex CLI not installed; this check never calls a model")
    _, _, context = store
    context.attempt.update(kind="resume", session_id="01234567-0123-4123-8123-012345678901")
    argv = CodexAdapter().command(context)["argv"]
    # Help only: the CLI parser validates the constructed argv with no paid run.
    result = subprocess.run([*argv[:-1], "--help"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("provider", ["claude", "unknown", ""])
def test_unavailable_provider_is_explicit_and_codex_loads_independently(provider):
    assert isinstance(get_adapter("codex"), CodexAdapter)
    with pytest.raises(UnsupportedProviderError, match="Unsupported provider"):
        get_adapter(provider)


# Every §6.5 row, including the two invalid-report rows, is applied by the real
# supervisor so retry, pause reason and pipeline behavior are checked as well.
@pytest.mark.parametrize("exit_code,report,kind,events,reason,expected", [
    (0, "APPROVE\n", "fresh", [], None, ("valid", None, "needs_human", "verify")),
    (0, None, "fresh", [], None, ("invalid", "contract", "running", None)),
    (0, "invalid", "fresh", [], None, ("invalid", "contract", "running", None)),
    (0, "invalid", "contract_retry", [], None, ("invalid", "contract", "needs_human", "contract")),
    (1, None, "fresh", [{
        "type": "turn.failed",
        "error": {
            "message": "401 Unauthorized"
        }
    }], None, ("failed", "auth", "paused", "auth")),
    (1, None, "fresh", [{
        "type": "turn.failed",
        "error": {
            "message": "permission denied; Bash: npm test --token=private"
        }
    }], None, ("failed", "permission", "paused", "permission")),
    (1, None, "fresh", [{
        "type": "turn.failed",
        "error": {
            "message": "max budget exceeded"
        }
    }], None, ("failed", "budget", "paused", "budget")),
    (1, None, "fresh", [{
        "type": "turn.failed",
        "error": {
            "message": "429 rate limit after retries"
        }
    }], None, ("failed", "rate_limit", "paused", "rate_limit")),
    (1, None, "fresh", [{
        "type": "turn.failed",
        "error": {
            "message": "server overloaded"
        }
    }], None, ("failed", "rate_limit", "paused", "rate_limit")),
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
    meta = json.loads((FIXTURES / (capture + ".meta.json")).read_text())
    (context.directory /
     "events.jsonl").write_bytes((FIXTURES / (capture + ".jsonl")).read_bytes() +
                                 (FIXTURES / (capture + ".stderr")).read_bytes())
    (context.directory / "report.md").write_text("APPROVE\n")
    outcome = supervisor.adapters["codex"].classify(context, {"exit_code": meta["exit_code"]})
    expected = ("failed", "auth") if capture == "auth" else (("failed",
                                                              "crash") if meta["exit_code"] else
                                                             ("valid", None))
    assert (outcome.state, outcome.error_class) == expected


def test_stale_report_and_recovered_diagnostics(store):
    _, supervisor, context = store
    adapter = supervisor.adapters["codex"]
    (context.directory / "report.md").write_text("APPROVE\n")
    context.attempt["t_launch"] = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    assert adapter.classify(context, {"exit_code": 0}).state == "invalid"
    context.attempt["t_launch"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    events = [{
        "type": "error",
        "message": "401 Unauthorized reconnecting"
    }, {
        "type": "turn.failed",
        "error": {
            "message": "rate limit"
        }
    }, {
        "type": "turn.completed"
    }]
    (context.directory / "events.jsonl").write_bytes(b"".join(encoded(e) for e in events))
    assert adapter.classify(context, {"exit_code": 0}).state == "valid"
    # Failed tools and assistant text are not terminal provider failures.
    events = [
        item("command_execution",
             command="echo 401",
             aggregated_output="401 Unauthorized",
             exit_code=1),
        item("agent_message", text="rate limit permission denied")
    ]
    (context.directory / "events.jsonl").write_bytes(b"".join(encoded(e) for e in events))
    assert adapter.classify(context, {"exit_code": 0}).state == "valid"


def test_terminal_reason_precedes_retry_noise_and_zero_exit_does_not_hide_failed_turn(store):
    _, supervisor, context = store
    (context.directory / "events.jsonl").write_bytes(
        encoded({
            "type": "error",
            "message": "401 Unauthorized reconnecting"
        }) + encoded({
            "type": "turn.failed",
            "error": {
                "message": "overloaded"
            }
        }))
    outcome = supervisor.adapters["codex"].classify(context, {"exit_code": 0})
    assert (outcome.state, outcome.error_class) == ("failed", "rate_limit")


@pytest.mark.parametrize("event,expected", [
    (item("command_execution", command="npm test\nsecond command"), "running `npm test`"),
    (item("command_execution", command="npm test " + "arg " * 30), "running `" +
     ("npm test " + "arg " * 30)[:79] + "…`"),
    (item("file_read", path="/repo/src/theme.ts"), "reading src/theme.ts"),
    (item("read", path="src/theme.ts"), "reading src/theme.ts"),
    (item("file_search", pattern="TODO"), 'searching "TODO"'),
    (item("file_change", changes=[{
        "path": "/repo/src/theme.ts"
    }]), "editing src/theme.ts"),
    (item("file_change", changes=[{
        "path": p
    } for p in ("a", "b", "c")]), "editing 3 files"),
    (item("command_execution", command="agent-browser open http://127.0.0.1:5172/settings"),
     "opening http://127.0.0.1:5172/settings"),
    (item("command_execution", command="agent-browser snapshot"), "checking snapshot"),
    (item("command_execution", command='git commit -m "Fix theme"'), 'committing "Fix theme"'),
    (item("file_change", changes=[{
        "path": "/repo/report.md"
    }]), "writing report"),
    (item("command_execution", command='echo APPROVE > /repo/report.md'), "writing report"),
    (item("agent_message", text="Finished"), "thinking"),
    (item("unknown"), None),
])
def test_summary_table(event, expected):
    assert CodexAdapter().summarize(event,
                                    worktree=Path("/repo"),
                                    report_path=Path("/repo/report.md"),
                                    quiet_seconds=20) == expected


def test_thinking_requires_quiet_and_incomplete_item_updates_do_not_erase_activity():
    adapter = CodexAdapter()
    assert adapter.summarize(item("agent_message", text="text"), quiet_seconds=19.99) is None
    started = item("command_execution", command="npm test")
    assert adapter.parse_event(encoded(started)).activity == "running `npm test`"
    assert adapter.parse_event(encoded({
        "type": "item.updated",
        "item": {
            "id": "item_0"
        }
    })).activity is None
    assert adapter.parse_event(encoded(started)) == adapter.parse_event(encoded(started))


def test_windows_relative_paths_shell_launcher_and_report_destination():
    adapter = CodexAdapter()
    root = Path("C:/Work Tree")
    assert adapter.summarize(item("read", path="c:\\work tree\\src\\theme.ts"),
                             worktree=root) == "reading src/theme.ts"
    assert adapter.summarize(item("file_change", changes=[{
        "path": "c:\\work tree\\report.md"
    }]),
                             worktree=root,
                             report_path=Path("C:/Work Tree/report.md")) == "writing report"
    assert adapter.summarize(
        item("command_execution",
             command='"C:\\Program Files\\pwsh.exe" -Command \'npm test\'')) == "running `npm test`"


@pytest.mark.parametrize("secret", [
    "--token=private", "--token private", '--api-key="two words"', "KEY=private", "API_KEY=private",
    "password='two words'", '"api_key": "private"', "Bearer private", "sk-privatekey",
    "bXlzZWNyZXQ" * 6
])
def test_secret_masking_across_activity_and_diagnostics(secret):
    safe = mask_secrets(secret)
    assert "…" in safe and "private" not in safe and "two words" not in safe
    assert "bXlzZWNyZXQ" not in safe
    adapter = CodexAdapter()
    summary = adapter.summarize(item("command_execution", command="npm test " + secret))
    assert "…" in summary and "private" not in summary
    update = adapter.parse_event(
        encoded({
            "type": "turn.failed",
            "error": {
                "message": "permission denied " + secret
            }
        }))
    assert "private" not in " ".join(update.errors + update.permission_denials)


def test_structured_denial_retains_masked_tool_and_command():
    update = CodexAdapter().parse_event(
        encoded({
            "type": "event",
            "permission_denials": [{
                "tool": "shell",
                "command": "npm test --token=private"
            }]
        }))
    assert "shell" in update.permission_denials[0] and "npm test" in update.permission_denials[0]
    assert "private" not in update.permission_denials[0]


def test_usage_accumulates_across_offsets_and_recovery_and_activity_stays_replay_safe(store):
    scheduler, supervisor, context = store
    path = context.directory / "events.jsonl"
    turn = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 4,
            "output_tokens": 2
        }
    }
    path.write_bytes(
        encoded({
            "type": "thread.started",
            "thread_id": "recorded-thread"
        }) + encoded(item("command_execution", command="npm test")) + encoded(turn))
    supervisor.tail(context)
    context = scheduler.context(context.repo_id, context.attempt["id"])
    assert context.attempt["session_id"] == "recorded-thread"
    with path.open("ab") as stream:
        stream.write(encoded(turn))
    # New adapter instance, as after restart; totals come from the durable offset.
    replacement = Supervisor(scheduler, {"codex": CodexAdapter()})
    replacement.tail(context)
    replacement.tail(context)  # same offset cannot double-count
    context = scheduler.context(context.repo_id, context.attempt["id"])
    usage = json.loads(context.attempt["usage"])
    assert usage == {
        "input": 20,
        "cached_input": 8,
        "output": 4,
        "cost_usd": None,
        "raw": [turn["usage"], turn["usage"]]
    }
    assert context.attempt["last_activity"] == "running `npm test`"


def test_assistant_only_run_can_show_thinking_after_twenty_seconds(store):
    scheduler, supervisor, context = store
    context.attempt["t_launch"] = (datetime.now(timezone.utc) - timedelta(seconds=21)).isoformat()
    (context.directory / "events.jsonl").write_bytes(
        encoded(item("agent_message", text="Considering the task")))
    supervisor.tail(context)
    context = scheduler.context(context.repo_id, context.attempt["id"])
    assert context.attempt["last_activity"] == "thinking"


def test_real_git_head_is_returned_and_unavailable_git_evidence_pauses(store, monkeypatch):
    _, _, context = store
    worktree = Path(context.job["worktree"])
    head = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                                   text=True).strip()
    (context.directory / "report.md").write_text("APPROVE\n")
    outcome = CodexAdapter().classify(context, {"exit_code": 0})
    assert outcome.completion.head_after == outcome.current_head == head
    assert outcome.completion.report_path == context.attempt["dir"] + "/report.md"

    def unavailable(*args):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(gitops, "validate_result", unavailable)
    outcome = CodexAdapter().classify(context, {"exit_code": 0})
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
    adapter = CodexAdapter()
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
    supervisor.adapters["codex"] = adapter
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
