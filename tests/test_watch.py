import json
import threading
import time
import unittest

from helpers import Repo, job

import server


class WatchTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_a_request_is_queued_once(self):
        job.request_start("Add a thing")
        job.request_start("Add a thing")
        self.assertEqual([r["item"] for r in job.pending_requests()], ["Add a thing"])

    def test_wait_claims_a_request_and_marks_the_watcher_busy(self):
        job.request_start("Add a thing")
        got = job.wait("w-test01", timeout=5)
        self.assertEqual(got["item"], "Add a thing")
        self.assertEqual(job.pending_requests(), [])
        beat = json.loads((job.JOBS / "watcher-w-test01.json").read_text(encoding="utf-8"))
        self.assertEqual(beat["busy"], "Add a thing")

    def test_wait_gives_up_after_its_timeout(self):
        started = time.time()
        self.assertIsNone(job.wait("w-test02", timeout=1))
        self.assertLess(time.time() - started, 4)
        self.assertEqual(job.idle_watchers(), ["w-test02"])

    def test_wait_picks_up_a_request_made_while_waiting(self):
        threading.Timer(1.0, job.request_start, args=("Late one", )).start()
        self.assertEqual(job.wait("w-test03", timeout=6)["item"], "Late one")

    def test_two_watchers_never_get_the_same_request(self):
        job.request_start("Only one")
        results = []
        errors = []

        def claim(watcher):
            try:
                results.append(job.wait(watcher, timeout=2))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim, args=(w, )) for w in ("w-a", "w-b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r["item"] for r in results if r), ["Only one"])

    def test_cancel_and_unwatch(self):
        job.request_start("Changed my mind")
        job.cancel_request("Changed my mind")
        self.assertEqual(job.pending_requests(), [])
        job.wait("w-test04", timeout=1)
        job.unwatch("w-test04")
        self.assertEqual(job.idle_watchers(), [])

    def test_bad_watcher_ids_are_refused(self):
        for bad in ["", "../x", "a b"]:
            with self.assertRaises(job.JobError):
                job.wait(bad, timeout=0)


class StartButtonTests(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()
        self._backlog, self._start = server.BACKLOG, server.start_headless
        server.BACKLOG = self.repo.factory / "backlog.md"
        server.BACKLOG.write_text("# Backlog\n\n- [ ] First\n- [ ] Second\n", encoding="utf-8")
        self.launched = []
        server.start_headless = lambda permissions: self.launched.append(permissions
                                                                         ) or "headless-test.log"

    def tearDown(self):
        server.BACKLOG, server.start_headless = self._backlog, self._start
        self.repo.close()

    def test_queue_mode_only_queues(self):
        server.edit_backlog({"action": "start", "index": 1})
        self.assertEqual([r["item"] for r in job.pending_requests()], ["Second"])
        self.assertEqual(self.launched, [])

    def test_headless_mode_launches_when_nobody_is_watching(self):
        job.save_config({"start": "headless"})
        result = server.edit_backlog({"action": "start", "index": 0})
        self.assertEqual(self.launched, ["auto"])
        self.assertEqual(result["headless"], "headless-test.log")
        server.edit_backlog({"action": "start", "index": 0})  # already queued: no second launch
        self.assertEqual(self.launched, ["auto"])

    def test_headless_mode_leaves_it_to_a_ready_watcher(self):
        job.save_config({"start": "headless", "headlessPermissions": "bypassPermissions"})
        job.write_json(job.JOBS / "watcher-w-ready.json", {
            "watcher": "w-ready",
            "at": job.now(),
            "busy": None
        })
        server.edit_backlog({"action": "start", "index": 0})
        self.assertEqual(self.launched, [])

    def test_cancel_removes_the_request(self):
        server.edit_backlog({"action": "start", "index": 0})
        server.edit_backlog({"action": "cancel", "index": 0})
        self.assertEqual(job.pending_requests(), [])

    def test_start_settings_are_checked(self):
        for bad in [{"start": "now"}, {"headlessPermissions": "plan"}]:
            with self.assertRaises(job.JobError):
                job.save_config(bad)


if __name__ == "__main__":
    unittest.main()
