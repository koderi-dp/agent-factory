import json
import subprocess
import sys
import threading
import time
import unittest

from helpers import Repo, git, job


class JobStateTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()
        (job.JOBS / "001-x").mkdir()
        job.new("001-x", "Do a thing", "C:\\work\\001-x")

    def tearDown(self):
        self.repo.close()

    def test_new_writes_a_full_job(self):
        j = job.load("001-x")
        self.assertEqual(j["stage"], "spec")
        self.assertEqual(j["worktree"], "C:/work/001-x")
        self.assertEqual(j["reviews"], {k: "pending" for k in job.LENSES})
        self.assertEqual([h["stage"] for h in j["history"]], ["spec"])

    def test_new_refuses_an_unclaimed_or_existing_job(self):
        with self.assertRaises(job.JobError):
            job.new("002-unclaimed", "x", "C:/w")
        with self.assertRaises(job.JobError):
            job.new("001-x", "again", "C:/w")

    def test_stage_changes_append_history_with_the_round(self):
        job.update("001-x", [("stage", "build")])
        job.update("001-x", [("stage", "review"), ("round", "1"), ("reviews", "pending")])
        job.update("001-x", [("reviews.ux", "PASS")])  # no stage change, no history entry
        history = job.load("001-x")["history"]
        self.assertEqual([h["stage"] for h in history], ["spec", "build", "review"])
        self.assertEqual(history[-1]["round"], 1)

    def test_a_stage_change_clears_the_note_unless_it_sets_one(self):
        job.update("001-x", [("stage", "approved"), ("note", "checkout busy")])
        self.assertEqual(job.load("001-x")["note"], "checkout busy")
        job.update("001-x", [("stage", "merged")])
        self.assertNotIn("note", job.load("001-x"))

    def test_bad_values_change_nothing(self):
        before = job.load("001-x")
        for change in [("stage", "done"), ("reviews.ux", "MAYBE"), ("reviews.qa", "PASS"),
                       ("colour", "red")]:
            with self.assertRaises(job.JobError):
                job.update("001-x", [change])
        self.assertEqual(job.load("001-x")["history"], before["history"])

    def test_bad_job_ids_are_refused(self):
        for bad in ["", "../x", "a/b", ".hidden"]:
            with self.assertRaises(job.JobError):
                job.load(bad)

    def test_parallel_writers_lose_nothing(self):
        threads = [
            threading.Thread(target=job.update, args=("001-x", [(f"heads.{i}", f"c{i}")]))
            for i in range(30)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(job.load("001-x")["heads"]), 30)
        self.assertFalse((job.JOBS / "001-x" / ".lock").exists())


class MergeTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_merges_and_cleans_up(self):
        worktree = self.repo.add_job("001-ok")
        message = job.merge("001-ok")
        self.assertIn("merged factory/001-ok", message)
        self.assertEqual(job.load("001-ok")["stage"], "merged")
        self.assertFalse(worktree.exists())
        self.assertEqual(git("branch", "--list", "factory/*", cwd=self.repo.path), "")
        self.assertIn("from the job", (self.repo.path / "app.txt").read_text(encoding="utf-8"))

    def test_unrelated_uncommitted_edits_dont_block(self):
        self.repo.add_job("001-ok")
        (self.repo.factory / "backlog.md").write_text("# Backlog\n- [ ] edited\n", encoding="utf-8")
        job.merge("001-ok")
        self.assertIn("edited", (self.repo.factory / "backlog.md").read_text(encoding="utf-8"))

    def test_edits_to_the_jobs_files_block(self):
        self.repo.add_job("001-clash")
        (self.repo.path / "app.txt").write_text("mine\n", encoding="utf-8")
        with self.assertRaises(job.Busy) as err:
            job.merge("001-clash")
        self.assertIn("app.txt", str(err.exception))
        self.assertEqual(job.load("001-clash")["stage"], "approved")

    def test_staged_changes_block(self):
        self.repo.add_job("001-staged", file="other.txt")
        (self.repo.path / "app.txt").write_text("staged\n", encoding="utf-8")
        git("add", "app.txt", cwd=self.repo.path)
        with self.assertRaisesRegex(job.Busy, "staged"):
            job.merge("001-staged")

    def test_another_branch_blocks(self):
        self.repo.add_job("001-x")
        git("checkout", "-q", "-b", "elsewhere", cwd=self.repo.path)
        with self.assertRaisesRegex(job.Busy, "elsewhere"):
            job.merge("001-x")

    def test_a_conflict_is_undone_and_keeps_unrelated_edits(self):
        self.repo.add_job("001-conflict")
        (self.repo.path / "app.txt").write_text("one\nfrom main\n", encoding="utf-8")
        git("commit", "-q", "-am", "main moves on", cwd=self.repo.path)
        (self.repo.path / "notes.txt").write_text("wip\n", encoding="utf-8")
        git("add", "notes.txt", cwd=self.repo.path)
        git("commit", "-q", "-m", "notes", cwd=self.repo.path)
        (self.repo.path / "notes.txt").write_text("wip, edited\n", encoding="utf-8")
        with self.assertRaisesRegex(job.JobError, "undone"):
            job.merge("001-conflict")
        r = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                           cwd=self.repo.path,
                           capture_output=True)
        self.assertNotEqual(r.returncode, 0, "a merge was left in progress")
        self.assertEqual((self.repo.path / "notes.txt").read_text(encoding="utf-8"),
                         "wip, edited\n")
        self.assertEqual(job.load("001-conflict")["stage"], "approved")

    def test_a_server_left_running_in_the_worktree_doesnt_block_the_merge(self):
        # Job 095's ports are 6050-6058, clear of the real jobs' ports.
        worktree = self.repo.add_job("095-leftover")
        server = subprocess.Popen(
            [sys._base_executable, "-m", "http.server", "6050", "--bind", "127.0.0.1"],
            cwd=worktree,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if job.listeners([6050]):
                    break
                time.sleep(0.1)
            self.assertTrue(job.listeners([6050]), "the test server didn't start")
            self.assertIn(server.pid, job.listeners(job.job_ports("095-leftover")))
            job.merge("095-leftover")
            self.assertEqual(job.listeners([6050]), [])
            self.assertFalse(worktree.exists())
            self.assertNotIn("note", job.load("095-leftover"))
        finally:
            server.kill()

    def test_job_ports(self):
        self.assertEqual(job.job_ports("003-x"), list(range(5130, 5139)))
        self.assertEqual(job.job_ports("not-a-number"), [])

    def test_only_waiting_jobs_merge(self):
        self.repo.add_job("001-live", stage="build")
        with self.assertRaisesRegex(job.JobError, "only an approved"):
            job.merge("001-live")


class DeleteTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_removes_worktree_branch_and_folder(self):
        worktree = self.repo.add_job("001-gone", stage="needs-human")
        message = job.delete("001-gone")
        self.assertIn("worktree, branch, job folder", message)
        self.assertFalse(worktree.exists())
        self.assertFalse((job.JOBS / "001-gone").exists())
        self.assertEqual(git("branch", "--list", "factory/*", cwd=self.repo.path), "")

    def test_refuses_a_live_job_unless_forced(self):
        self.repo.add_job("001-live", stage="review")
        with self.assertRaisesRegex(job.JobError, "still running"):
            job.delete("001-live")
        job.delete("001-live", force=True)
        self.assertFalse((job.JOBS / "001-live").exists())


class ConfigTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_defaults_without_a_file(self):
        cfg = job.load_config()
        self.assertEqual(cfg["merge"], "auto")
        self.assertEqual(cfg["maxRounds"], 3)
        self.assertNotIn("models", cfg)
        self.assertEqual(cfg["pipeline"][0]["providers"]["claude"], {
            "model": "opus",
            "effort": "medium"
        })

    def test_saves_and_fills_in_missing_keys(self):
        cfg = job.check_config({"merge": "verify"})
        cfg["pipeline"][1]["providers"]["claude"]["model"] = "sonnet"
        cfg["pipeline"][-1]["providers"]["claude"]["effort"] = "max"
        job.save_config(cfg)
        cfg = json.loads(job.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["pipeline"][1]["providers"]["claude"]["model"], "sonnet")
        self.assertEqual(cfg["pipeline"][-1]["providers"]["claude"]["model"], "opus")
        self.assertNotIn("efforts", cfg)
        self.assertEqual(cfg["pipeline"][-1]["providers"]["claude"]["effort"], "max")
        self.assertEqual(cfg["merge"], "verify")

    def test_refuses_bad_settings(self):
        for bad in [{
                "models": {
                    "builder": "gpt"
                }
        }, {
                "models": {
                    "nobody": "opus"
                }
        }, {
                "efforts": {
                    "builder": "turbo"
                }
        }, {
                "merge": "sometimes"
        }, {
                "maxRounds": 0
        }, {
                "maxRounds": 6
        }, {
                "maxRounds": True
        }, []]:
            with self.assertRaises(job.JobError, msg=bad):
                job.save_config(bad)
        self.assertFalse(job.CONFIG.exists())


if __name__ == "__main__":
    unittest.main()
