"""Provider isolation, Codex launcher recovery and generated role validation."""

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from helpers import ROOT, Repo, job

import codex_runner
import server


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)

    def test_settings_keep_clients_separate(self):
        cfg = job.check_config({"provider": "codex"})
        cfg["pipeline"][1]["providers"] = {
            "claude": {"model": "sonnet", "effort": "medium"},
            "codex": {"model": "gpt-6-luna", "effort": "high"},
        }
        cfg = job.save_config(cfg)
        self.assertEqual(cfg["pipeline"][1]["providers"]["codex"]["model"], "gpt-6-luna")
        self.assertEqual(cfg["pipeline"][1]["providers"]["claude"]["model"], "sonnet")
        cfg["provider"] = "claude"
        job.save_config(cfg)
        self.assertEqual(job.load_config()["pipeline"][1]["providers"]["codex"]["effort"], "high")
        for bad in ({"provider": "other"}, {"codexModels": {"builder": "opus"}},
                    {"codexEfforts": {"builder": "turbo"}}, {"codexModels": []}):
            with self.assertRaises(job.JobError):
                job.check_config(bad)

    def test_codex_request_only_reaches_codex_watcher(self):
        job.save_config({"provider": "codex"})
        job.request_start("Codex feature")
        self.assertIsNone(job.wait("w-claude", timeout=0.02))
        self.assertEqual(len(job.pending_requests()), 1)
        self.assertEqual(job.wait("w-codex", timeout=2, provider="codex")["item"], "Codex feature")
        self.assertEqual(job.idle_watchers("codex"), [])
        self.assertEqual(job.idle_watchers("claude"), ["w-claude"])

    def test_job_records_provider(self):
        (job.JOBS / "001-codex").mkdir()
        job.new("001-codex", "feature", str(self.repo.path), provider="codex")
        self.assertEqual(job.load("001-codex")["provider"], "codex")
        with self.assertRaises(job.JobError):
            job.update("001-codex", [("provider", "other")])

    @patch.object(codex_runner.shutil, "which", return_value="codex")
    def test_launch_command_and_specific_resume(self, _which):
        args = codex_runner.command(self.repo.path, "auto")
        self.assertIn("--approve-for-me", args)
        self.assertNotIn("--sandbox", args)
        self.assertIn("--add-dir", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        resumed = codex_runner.command(self.repo.path, "auto", "test-session-123")
        self.assertIn("resume", resumed)
        self.assertIn("test-session-123", resumed)
        self.assertNotIn("--last", resumed)
        bypass = codex_runner.command(self.repo.path, "bypassPermissions")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", bypass)
        with self.assertRaises(job.JobError):
            codex_runner.command(self.repo.path, "auto", "--last")

    @patch.object(codex_runner.shutil, "which", return_value="codex")
    def test_runner_captures_session_and_failed_exit_for_recovery(self, _which):
        session = job.JOBS / "headless-test.session.json"
        events = '{"type":"thread.started","thread_id":"exact-session-123"}\n'
        fake = Mock(pid=42, stdout=io.StringIO(events + "diagnostic\n"))
        fake.wait.return_value = 1
        with patch.object(job, "repo_root", return_value=self.repo.path), patch.object(codex_runner.subprocess, "Popen", return_value=fake):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(codex_runner.run(session, "auto"), 1)
        state = json.loads(session.read_text())
        self.assertEqual((state["sessionId"], state["status"], state["exitCode"]), ("exact-session-123", "failed", 1))
        fake = Mock(pid=43, stdout=io.StringIO('{"type":"turn.completed"}\n'))
        fake.wait.return_value = 0
        with patch.object(job, "repo_root", return_value=self.repo.path), patch.object(codex_runner.subprocess, "Popen", return_value=fake) as launch:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(codex_runner.run(session, resume=True), 0)
        args = launch.call_args.args[0]
        self.assertEqual(launch.call_args.kwargs["env"]["PYTHONUTF8"], "1")
        self.assertIn("exact-session-123", args)
        self.assertNotIn("--last", args)
        self.assertEqual(json.loads(session.read_text())["status"], "finished")

    @patch.object(codex_runner.shutil, "which", return_value="codex")
    def test_runner_stops_child_and_records_stream_failure(self, _which):
        class FailedStream(io.StringIO):
            def __next__(self):
                raise OSError("event stream failed")

        session = job.JOBS / "headless-error.session.json"
        fake = Mock(pid=42, stdout=FailedStream())
        fake.poll.return_value = None
        fake.wait.return_value = 0
        with patch.object(job, "repo_root", return_value=self.repo.path), patch.object(codex_runner.subprocess, "Popen", return_value=fake):
            with self.assertRaisesRegex(OSError, "event stream failed"):
                codex_runner.run(session, "auto")
        fake.terminate.assert_called_once()
        self.assertTrue(fake.stdout.closed)
        self.assertEqual(json.loads(session.read_text())["status"], "failed")

    def test_server_launches_selected_client(self):
        for provider in ("claude", "codex"):
            job.save_config({"provider": provider})
            skill = self.repo.path / (".agents/skills/factory/SKILL.md" if provider == "codex" else ".claude/skills/factory/SKILL.md")
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text("installed", encoding="utf-8")
            with patch.object(job, "repo_root", return_value=self.repo.path), patch.object(server.shutil, "which", return_value=provider), patch.object(server.subprocess, "Popen") as launch:
                log = server.start_headless("auto")
            args = launch.call_args.args[0]
            self.assertTrue(log.endswith(".log"))
            if provider == "codex":
                self.assertEqual(args[0], sys.executable)
                self.assertTrue(args[1].endswith("codex_runner.py"))
                self.assertTrue(args[3].endswith(".session.json"))
            else:
                self.assertEqual(args[:3], ["claude", "-p", "/factory watch --once"])

    def test_failed_headless_launch_can_be_retried(self):
        backlog = self.repo.factory / "backlog.md"
        backlog.write_text("- [ ] Try again\n", encoding="utf-8")
        job.save_config({"provider": "codex", "start": "headless"})
        with patch.object(server, "BACKLOG", backlog), patch.object(server, "start_headless", side_effect=job.JobError("missing tool")):
            with self.assertRaisesRegex(job.JobError, "missing tool"):
                server.edit_backlog({"action": "start", "index": 0})
        self.assertEqual(job.pending_requests(), [])


class GeneratedCodexTests(unittest.TestCase):
    def test_generated_files_are_current_and_roles_parse(self):
        spec = importlib.util.spec_from_file_location("sync_codex", ROOT / "tools/sync_codex.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for relative, expected in module.generated().items():
            self.assertEqual((ROOT / relative).read_text(encoding="utf-8"), expected, str(relative))
        repo = Repo()
        self.addCleanup(repo.close)
        for role in job.AGENTS:
            data = job.pipeline.agent_file(repo.path, "codex", role)
            self.assertIn(data["type"], {"task", "review", "approval"})
            self.assertIn("Your file is the hand-off", (repo.path / data["instructions"]).read_text(encoding="utf-8"))
            self.assertFalse((ROOT / f"codex/agents/{role}.toml").exists())



if __name__ == "__main__":
    unittest.main()
