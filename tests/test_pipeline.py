"""Exercise configured transitions and real Git/report evidence without an LLM."""

import copy
import unittest
from pathlib import Path

from helpers import Repo, execution, git, job, write_agent

import pipeline

CUSTOM = {
    "writer": {
        "type": "task",
        "output": "draft.md"
    },
    "quality": {
        "type": "review",
        "output": "quality.md"
    },
    "risk": {
        "type": "review",
        "output": "risk.md"
    },
    "gate": {
        "type": "approval",
        "output": "signoff.md"
    },
}
STEPS = [
    execution("draft", "writer"),
    {
        "id": "checks",
        "parallel": [execution("quality", "quality"),
                     execution("risk", "risk")],
        "onChanges": "draft"
    },
    execution("signoff", "gate"),
]


class PipelineTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        for provider in ("claude", "codex"):
            for role, metadata in CUSTOM.items():
                write_agent(self.repo, role, metadata["type"], metadata["output"], provider)
        self.cfg = job.save_config({"pipeline": STEPS, "merge": "verify", "maxRounds": 2})
        self.worktree = self.repo.add_job("001-pipe", stage="draft")

    def plan(self):
        return job.step_plan("001-pipe")

    def finish(self, overrides=None):
        for task in self.plan()["tasks"]:
            first = {
                "task": "STATUS: READY",
                "review": "VERDICT: PASS",
                "approval": "APPROVE"
            }[task["type"]]
            first = (overrides or {}).get(task["agent"], first)
            Path(task["output"]).write_text(first + "\n\nEvidence.\n", encoding="utf-8")
        return job.advance("001-pipe")

    def test_custom_pipeline_finishes_and_merges(self):
        self.assertEqual(self.finish()["stage"], "checks")
        self.assertEqual(self.finish()["stage"], "signoff")
        done = self.finish()
        self.assertEqual(done["stage"], "approved")
        self.assertEqual(len(done["runs"]), 3)
        self.assertEqual(done["reviews"], {"quality": "PASS", "risk": "PASS"})
        archived = done["runs"][1]["reports"]["quality"]["archive"]
        self.assertTrue((job.JOBS / "001-pipe" / archived).is_file())
        job.merge("001-pipe")
        self.assertEqual(job.load("001-pipe")["stage"], "merged")

    def test_retry_is_bounded_and_invalidates_downstream_results(self):
        self.finish()
        state = self.finish({"quality": "VERDICT: CHANGES"})
        self.assertEqual((state["stage"], state["resumes"]), ("draft", 1))
        self.assertNotIn("checks", state["results"])
        self.finish()
        state = self.finish({"quality": "VERDICT: CHANGES"})
        self.assertEqual(state["stage"], "needs-human")
        self.assertEqual(len(state["runs"]), 4)
        job.rework("001-pipe", "Fix the custom review")
        state = job.load("001-pipe")
        self.assertEqual((state["stage"], state["resumes"]), ("draft", 0))
        self.assertEqual((job.JOBS / "001-pipe/rework-1.md").read_text(), "Fix the custom review\n")

    def test_pipeline_and_model_settings_are_frozen(self):
        self.cfg["pipeline"][0]["providers"]["claude"]["model"] = "sonnet"
        self.cfg.update(pipeline=[self.cfg["pipeline"][0], self.cfg["pipeline"][-1]],
                        merge="auto",
                        maxRounds=5)
        job.save_config(self.cfg)
        plan = self.plan()
        self.assertEqual(plan["tasks"][0]["model"], "opus")
        self.repo.add_job("002-new", stage="draft")
        self.assertEqual(job.step_plan("002-new")["tasks"][0]["model"], "sonnet")
        state = job.load("001-pipe")
        self.assertEqual(len(state["pipeline"]), 3)
        self.assertEqual((state["mergePolicy"], state["maxRounds"]), ("verify", 2))
        self.assertEqual(self.finish()["stage"], "checks")

    def test_resume_only_dispatches_missing_reviewers(self):
        self.finish()
        plan = self.plan()
        Path(plan["tasks"][0]["output"]).write_text("VERDICT: PASS\n", encoding="utf-8")
        resumed = self.plan()
        self.assertEqual([t["agent"] for t in resumed["tasks"]], ["risk"])
        self.assertEqual(job.load("001-pipe")["reviews"]["quality"], "PASS")
        self.finish()

    def test_parallel_reviewers_use_their_own_provider_settings(self):
        steps = self.cfg["pipeline"]
        steps[1]["parallel"][0]["id"] = "quality-check"
        steps[1]["parallel"][0]["providers"]["codex"] = {"model": "gpt-6-luna", "effort": "low"}
        steps[1]["parallel"][1]["providers"]["codex"] = {"model": "gpt-6-astra", "effort": "high"}
        job.save_config(self.cfg)
        self.repo.add_job("002-codex", stage="draft", provider="codex")
        plan = job.step_plan("002-codex")
        Path(plan["tasks"][0]["output"]).write_text("STATUS: READY\n", encoding="utf-8")
        job.advance("002-codex")
        tasks = job.step_plan("002-codex")["tasks"]
        self.assertEqual([(t["id"], t["agent"], t["model"], t["effort"]) for t in tasks], [
            ("quality-check", "quality", "gpt-6-luna", "low"),
            ("risk", "risk", "gpt-6-astra", "high"),
        ])

    def test_reusing_a_role_in_later_steps_keeps_separate_settings(self):
        polish = execution("polish", "writer")
        polish["providers"]["claude"] = {"model": "haiku", "effort": "low"}
        self.cfg["pipeline"].insert(1, polish)
        job.save_config(self.cfg)
        self.repo.add_job("002-reuse", stage="draft")
        plan = job.step_plan("002-reuse")
        self.assertEqual(plan["tasks"][0]["model"], "opus")
        Path(plan["tasks"][0]["output"]).write_text("STATUS: READY\n", encoding="utf-8")
        job.advance("002-reuse")
        plan = job.step_plan("002-reuse")
        self.assertEqual((plan["tasks"][0]["id"], plan["tasks"][0]["model"]), ("polish", "haiku"))

    def test_missing_selected_provider_prevents_job_creation(self):
        del self.cfg["pipeline"][0]["providers"]["codex"]
        job.save_config(self.cfg)
        (job.JOBS / "002-missing").mkdir()
        with self.assertRaisesRegex(job.JobError, "missing codex settings"):
            job.new("002-missing", "feature", str(self.repo.path), provider="codex")
        self.assertFalse(job.path_of("002-missing").exists())

    def test_stale_and_invalid_reports_do_not_advance(self):
        plan = self.plan()
        with self.assertRaises(job.JobError):
            job.advance("001-pipe")
        Path(plan["tasks"][0]["output"]).write_text("not a contract\n", encoding="utf-8")
        with self.assertRaisesRegex(job.JobError, "first line"):
            job.advance("001-pipe")
        self.finish()
        self.finish({"quality": "VERDICT: CHANGES"})
        self.plan()
        with self.assertRaisesRegex(job.JobError, "fresh report"):
            job.advance("001-pipe")

    def test_blocked_task_cannot_be_merged(self):
        self.assertEqual(self.finish({"writer": "STATUS: BLOCKED"})["stage"], "needs-human")
        with self.assertRaises(job.JobError):
            job.merge("001-pipe", human=True)

    def test_configured_steps_cannot_be_skipped(self):
        job.update("001-pipe", [("stage", "signoff")])
        with self.assertRaisesRegex(job.JobError, "prerequisite step draft"):
            self.plan()

    def test_missing_custom_instructions_are_reported(self):
        (self.repo.path / ".claude/agents/writer.md").unlink()
        with self.assertRaisesRegex(job.JobError, "missing instructions for writer"):
            self.plan()

    def test_selected_provider_resolves_its_own_markdown_file(self):
        write_agent(self.repo, "writer", "task", "codex-draft.md", "codex")
        (self.repo.path / ".claude/agents/writer.md").unlink()
        self.repo.add_job("002-codex", stage="draft", provider="codex")
        task = job.step_plan("002-codex")["tasks"][0]
        self.assertEqual(
            Path(task["instructions"]).resolve(),
            (self.repo.path / ".codex/agents/writer.md").resolve())
        self.assertEqual(Path(task["output"]).name, "codex-draft.md")
        self.assertNotIn("custom", task)

    def test_missing_provider_file_does_not_fall_back_to_other_provider(self):
        (self.repo.path / ".codex/agents/writer.md").unlink()
        (job.JOBS / "002-missing").mkdir()
        with self.assertRaisesRegex(job.JobError, r"\.codex[/\\]agents[/\\]writer\.md"):
            job.new("002-missing", "feature", str(self.repo.path), provider="codex")
        self.assertFalse(job.path_of("002-missing").exists())

    def test_commit_after_approval_cannot_merge(self):
        self.finish()
        self.finish()
        self.finish()
        (self.worktree / "app.txt").write_text("later commit\n", encoding="utf-8")
        git("add", "app.txt", cwd=self.worktree)
        git("commit", "-qm", "Later change", cwd=self.worktree)
        with self.assertRaisesRegex(job.JobError, "changed after approval"):
            job.merge("001-pipe", human=True)

    def test_escalation_needs_explicit_human_acceptance(self):
        self.finish()
        self.finish()
        self.assertEqual(self.finish({"gate": "ESCALATE"})["stage"], "needs-human")
        with self.assertRaises(job.JobError):
            job.merge("001-pipe")
        job.merge("001-pipe", human=True)

    def test_new_commit_invalidates_review_and_approval(self):
        self.finish()
        self.plan()
        path = self.worktree / "app.txt"
        path.write_text("changed during review\n", encoding="utf-8")
        git("add", "app.txt", cwd=self.worktree)
        git("commit", "-qm", "Unexpected edit", cwd=self.worktree)
        with self.assertRaisesRegex(job.JobError, "branch changed"):
            self.finish()

    def test_default_pipeline_runs_through_all_builtin_steps(self):
        job.save_config({})
        self.repo.add_job("002-default", stage="spec")
        for expected in ("build", "review", "approve", "approved"):
            plan = job.step_plan("002-default")
            for task in plan["tasks"]:
                first = {
                    "spec": "# Spec",
                    "status": "STATUS: READY",
                    "verdict": "VERDICT: PASS",
                    "decision": "APPROVE"
                }[task["contract"]]
                Path(task["output"]).write_text(first + "\n\nEvidence\n", encoding="utf-8")
            self.assertEqual(job.advance("002-default")["stage"], expected)

    def test_task_after_review_cannot_silently_invalidate_evidence(self):
        self.cfg["pipeline"].insert(2, execution("polish", "writer"))
        job.save_config(self.cfg)
        self.repo.add_job("002-late-task", stage="draft")
        for _ in range(3):
            plan = job.step_plan("002-late-task")
            if plan["stage"] == "polish":
                (self.repo.tmp / "app-factory/002-late-task/app.txt").write_text("polished\n",
                                                                                 encoding="utf-8")
                worktree = self.repo.tmp / "app-factory/002-late-task"
                git("add", "app.txt", cwd=worktree)
                git("commit", "-qm", "Polish after review", cwd=worktree)
            for task in plan["tasks"]:
                Path(task["output"]).write_text(
                    ("VERDICT: PASS" if task["type"] == "review" else "STATUS: READY") + "\n",
                    encoding="utf-8")
            job.advance("002-late-task")
        plan = job.step_plan("002-late-task")
        Path(plan["tasks"][0]["output"]).write_text("APPROVE\n", encoding="utf-8")
        with self.assertRaisesRegex(job.JobError, "changed after checks"):
            job.advance("002-late-task")


class PipelineValidationTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        for role, metadata in CUSTOM.items():
            write_agent(self.repo, role, metadata["type"], metadata["output"])

    def test_invalid_agent_metadata_is_rejected(self):
        path = self.repo.path / ".claude/agents/writer.md"
        invalid = [
            "Instructions without frontmatter.",
            "---\ntype: review\n---\nInstructions.",
            "---\ntype: unknown\noutput: draft.md\n---\nInstructions.",
            "---\ntype: task\ntype: review\noutput: draft.md\n---\nInstructions.",
            "---\ntype: task\noutput: ../draft.md\n---\nInstructions.",
            "---\ntype: task\noutput: rework-1.md\n---\nInstructions.",
            "---\ntype: task\noutput: request.md\n---\nInstructions.",
            "---\ntype: task\noutput: draft.md\ncontract: decision\n---\nInstructions.",
            "---\ntype: task\noutput: draft.md\n---\n",
        ]
        for text in invalid:
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(job.JobError):
                job.check_config({"pipeline": STEPS})

    def test_duplicate_report_outputs_are_rejected(self):
        write_agent(self.repo, "quality", "review", "DRAFT.md")
        with self.assertRaisesRegex(job.JobError, "duplicate report filename"):
            job.check_config({"pipeline": STEPS})

    def test_quoted_frontmatter_values_are_supported(self):
        path = self.repo.path / ".claude/agents/writer.md"
        path.write_text('---\ntype: "task" # role\noutput: \'draft.md\'\n---\nInstructions.\n',
                        encoding="utf-8")
        cfg = job.check_config({"pipeline": STEPS})
        self.assertEqual(cfg["pipeline"][0]["uses"], "writer")

    def test_provider_settings_are_explicit_and_validated(self):
        for selection in ({}, {
                "model": "opus"
        }, {
                "model": "default",
                "effort": "medium"
        }, {
                "model": "opus",
                "effort": "default"
        }, {
                "model": "gpt-6-luna",
                "effort": "low"
        }, {
                "model": [],
                "effort": "medium"
        }):
            step = execution("spec", "spec-writer")
            step["providers"]["claude"] = selection
            with self.subTest(selection=selection), self.assertRaises(job.JobError):
                job.check_config({"pipeline": [step, execution("gate", "approver")]})
        invalid = [
            {
                "id": "spec",
                "uses": "spec-writer"
            },
            {
                **execution("spec", "spec-writer"), "providers": {}
            },
            {
                **execution("spec", "spec-writer"), "providers": {
                    "other": {
                        "model": "opus",
                        "effort": "medium"
                    }
                }
            },
            {
                "id": "checks",
                "parallel": ["reviewer-code"]
            },
            {
                "id": "checks",
                "parallel": [execution("same", "reviewer-code"),
                             execution("same", "reviewer-ui")]
            },
        ]
        for step in invalid:
            with self.subTest(step=step), self.assertRaises(job.JobError):
                job.check_config({"pipeline": [step, execution("gate", "approver")]})

    def test_defaults_and_builtin_selection(self):
        cfg = job.check_config({})
        self.assertEqual(cfg["pipeline"], pipeline.DEFAULT)
        cfg = job.check_config(
            {"pipeline": [execution("code", "builder"),
                          execution("gate", "approver")]})
        self.assertEqual(cfg["pipeline"][0]["id"], "code")

    def test_invalid_graphs_and_paths_are_rejected(self):
        bad = [[], [{
            "id": "x",
            "uses": "unknown"
        }], [STEPS[0], STEPS[0], STEPS[-1]], [{
            "id": "merged",
            "uses": "writer"
        }, STEPS[-1]], [STEPS[0], {
            "id": "check",
            "parallel": ["writer", "quality"]
        }, STEPS[-1]],
               [STEPS[0], {
                   "id": "check",
                   "uses": "quality",
                   "onChanges": "signoff"
               }, STEPS[-1]], [STEPS[-1], STEPS[0]], [STEPS[0]]]
        for steps in bad:
            with self.subTest(steps=steps), self.assertRaises(job.JobError):
                job.check_config({"pipeline": steps})
        for name in ("../outside", "/absolute", "C:/outside", "factory/../outside",
                     "accessibility.md"):
            with self.subTest(name=name), self.assertRaises(job.JobError):
                job.check_config({"pipeline": [execution("unsafe", name), STEPS[-1]]})


if __name__ == "__main__":
    unittest.main()
