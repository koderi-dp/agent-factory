"""Plan sequencing and reviewer subprocess contracts, without live model calls."""

import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "run_plan", Path(__file__).resolve().parents[1] / "tools" / "run_plan.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PlanRunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        for name, value in (("REPO", self.repo), ("LOGS", self.repo / ".git" / "plan-run"),
                            ("DETAIL", "quiet")):
            self.patch(patch.object(runner, name, value))
        self.patch(patch("builtins.print"))

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def fake_codex(self, final="No findings.", code=0):
        self.patch(patch.object(runner.shutil, "which", return_value="codex"))
        process = Mock(stdin=io.StringIO(), stdout=io.StringIO(''))
        process.wait.return_value = code

        def launch(args, **kwargs):
            Path(args[args.index("-o") + 1]).write_text(final, encoding="utf-8")
            return process

        return self.patch(patch.object(runner.subprocess, "Popen", side_effect=launch))

    def test_review_uses_native_command_astra_and_captures_final(self):
        launch = self.fake_codex()
        report = self.repo / "docs" / "reviews" / "phase-1.md"
        ok, reason = runner.run_codex("review", "Review git diff abc..HEAD", runner.MODELS["Astra"],
                                      "xhigh", "auto", 1, review_report=report)
        self.assertTrue(ok, reason)
        args = launch.call_args.args[0]
        self.assertEqual(args[1], "exec")
        self.assertIn("review", args)
        self.assertIn("--approve-for-me", args)
        self.assertIn("gpt-6-astra", args)
        self.assertNotIn("--base", args)
        self.assertEqual(args[-1], "-")
        self.assertEqual(report.read_text(), "No findings.")

    def test_failed_review_does_not_publish_report(self):
        self.fake_codex(code=1)
        report = self.repo / "phase.md"
        self.assertFalse(runner.run_codex("review", "Review", "astra", "high", "bypass", 1,
                                          review_report=report)[0])
        self.assertFalse(report.exists())

    def test_empty_review_does_not_publish_report(self):
        self.fake_codex(final="  ")
        report = self.repo / "phase.md"
        self.assertFalse(runner.run_codex("review", "Review", "astra", "high", "bypass", 1,
                                          review_report=report)[0])
        self.assertFalse(report.exists())

    def test_build_still_requires_result_marker(self):
        self.fake_codex()
        self.assertFalse(runner.run_codex("build", "Build", "sol", "high", "auto", 1)[0])

    def test_build_accepts_pass(self):
        self.fake_codex(final="Done.\nSESSION-RESULT: PASS")
        self.assertTrue(runner.run_codex("build", "Build", "sol", "high", "auto", 1)[0])

    def test_codex_review_failure_does_not_start_fixes_or_claude(self):
        self.patch(patch.object(runner, "git", side_effect=["", "abc123"]))
        run = self.patch(patch.object(runner, "run_codex", return_value=(False, "failed")))
        which = self.patch(patch.object(runner.shutil, "which"))
        self.assertFalse(runner.review_and_fix(1, [{"id": "1.1"}], "auto", 1))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[2], runner.MODELS["Astra"])
        which.assert_not_called()

    def test_claude_nonzero_exit_does_not_publish_report_or_fix(self):
        self.patch(patch.object(runner, "git", side_effect=["", "abc123"]))
        self.patch(patch.object(runner.shutil, "which", return_value="claude"))
        self.patch(patch.object(runner.subprocess, "run", return_value=
                              subprocess.CompletedProcess([], 1, "rate limit", "")))
        fix = self.patch(patch.object(runner, "run_codex"))
        self.assertFalse(runner.review_and_fix(1, [{"id": "1.1"}], "auto", 1, "claude"))
        fix.assert_not_called()
        self.assertFalse((self.repo / "docs/reviews/phase-1.md").exists())

    def test_claude_timeout_stops_cleanly(self):
        self.patch(patch.object(runner, "git", side_effect=["", "abc123"]))
        self.patch(patch.object(runner.shutil, "which", return_value="claude"))
        self.patch(patch.object(runner.subprocess, "run", side_effect=
                              subprocess.TimeoutExpired("claude", 60)))
        fix = self.patch(patch.object(runner, "run_codex"))
        self.assertFalse(runner.review_and_fix(1, [{"id": "1.1"}], "auto", 1, "claude"))
        fix.assert_not_called()

    def test_none_never_launches_a_review(self):
        run = self.patch(patch.object(runner, "run_codex"))
        self.assertTrue(runner.review_and_fix(1, [], "auto", 1, "none"))
        run.assert_not_called()

    def test_parity_has_separate_review_marker(self):
        self.assertNotEqual(runner.review_marker(1, [{"id": "1.1"}]),
                            runner.review_marker(1, [{"id": "1.1"}, {"id": "1.11"}]))

    def test_dirty_fixes_are_failure(self):
        report = self.repo / "docs/reviews/phase-1.md"
        report.parent.mkdir(parents=True)
        report.write_text("No findings.")
        self.patch(patch.object(runner, "git", side_effect=["", "abc123", "fixcommit"]))
        self.patch(patch.object(runner, "run_codex", return_value=(True, "")))
        self.patch(patch.object(runner, "clean", return_value=False))
        self.assertFalse(runner.review_and_fix(1, [{"id": "1.1"}], "auto", 1))

    def run_main(self, argv, sessions, done=False, git=""):
        self.patch(patch.object(sys, "argv", ["run_plan.py", *argv]))
        self.patch(patch.object(sys, "stdout", Mock()))
        self.patch(patch.object(runner, "sessions", return_value=sessions))
        self.patch(patch.object(runner, "done", return_value=done))
        self.patch(patch.object(runner, "git", return_value=git))
        self.patch(patch.object(runner, "clean", return_value=True))
        run = self.patch(patch.object(runner, "run_session", return_value=True))
        review = self.patch(patch.object(runner, "review_and_fix", return_value=True))
        self.assertEqual(runner.main(), 0)
        return run, review

    @staticmethod
    def session(sid="1.1", requires_claude=False):
        return dict(id=sid, phase=1, title="test", model="sol", effort="high", manual=False,
                    astra_fallback=False, requires_claude=requires_claude)

    def test_default_skips_claude_sessions(self):
        regular, deferred = self.session(), self.session("1.11", True)
        run, review = self.run_main([], [regular, deferred])
        self.assertEqual(run.call_args.args[0], regular)
        run.assert_called_once()
        self.assertEqual(review.call_args.args[-1], "codex")

    def test_include_claude_runs_deferred_session(self):
        run, review = self.run_main(["--include-claude"], [self.session("1.11", True)])
        run.assert_called_once()
        self.assertEqual(review.call_args.args[1][0]["id"], "1.11")

    def test_unfinished_review_runs_even_when_sessions_done(self):
        run, review = self.run_main([], [self.session()], done=True)
        run.assert_not_called()
        review.assert_called_once()

    def test_none_and_legacy_flag_skip_review(self):
        run, review = self.run_main(["--no-review"], [self.session()])
        run.assert_called_once()
        review.assert_not_called()

    def test_real_prompt_parser_marks_only_1_11_as_deferred(self):
        actual = runner.sessions()
        self.assertEqual([s["id"] for s in actual if s["requires_claude"]], ["1.11"])
        self.assertFalse(next(s for s in actual if s["id"] == "1.6")["requires_claude"])


if __name__ == "__main__":
    unittest.main()
