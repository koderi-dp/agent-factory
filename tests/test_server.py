import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from helpers import Repo, job

import server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self._backlog = server.BACKLOG
        server.BACKLOG = self.repo.factory / "backlog.md"
        (self.repo.factory / "dashboard").mkdir()
        (self.repo.factory / "dashboard" / "index.html").write_text("<p>board</p>", encoding="utf-8")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.BACKLOG = self._backlog
        self.repo.close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}", "X-Factory": "1", "Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            data = json.loads(data)
        except ValueError:
            data = data.decode("utf-8", "replace")
        return res.status, data, res

    def test_root_and_the_old_address_lead_to_the_board(self):
        for path in ("/", "/dashboard.html"):
            status, _, res = self.request("GET", path)
            self.assertEqual((status, res.getheader("Location")), (302, "/dashboard/"))
        status, body, _ = self.request("GET", "/dashboard/")
        self.assertEqual((status, body), (200, "<p>board</p>"))

    def test_other_hosts_are_refused(self):
        status, _, _ = self.request("GET", "/dashboard/", host="evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("PUT", "/api/config", {}, host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_changes_need_the_factory_header(self):
        status, body, _ = self.request("PUT", "/api/config", {"merge": "verify"}, headers={"X-Factory": ""})
        self.assertEqual(status, 403)
        self.assertFalse(job.CONFIG.exists())

    def test_config_round_trip(self):
        status, body, _ = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertIn("haiku", body["models"])
        self.assertIn("max", body["efforts"])
        status, body, _ = self.request("PUT", "/api/config", {"merge": "verify", "efforts": {"builder": "high"}})
        self.assertEqual((status, body["config"]["merge"], body["config"]["efforts"]["builder"]), (200, "verify", "high"))
        status, body, _ = self.request("PUT", "/api/config", {"merge": "never"})
        self.assertEqual(status, 400)

    def test_merge_and_delete(self):
        self.repo.add_job("001-a")
        self.repo.add_job("002-b", stage="needs-human", file="b.txt")
        status, body, _ = self.request("POST", "/api/jobs/001-a/merge", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(job.load("001-a")["stage"], "merged")
        status, body, _ = self.request("POST", "/api/jobs/002-b/delete", {})
        self.assertEqual(status, 200, body)
        self.assertFalse((job.JOBS / "002-b").exists())
        status, _, _ = self.request("POST", "/api/jobs/..%2Fetc/delete", {})
        self.assertIn(status, (400, 404))

    def test_a_busy_checkout_answers_409(self):
        self.repo.add_job("001-a")
        (self.repo.path / "app.txt").write_text("mine\n", encoding="utf-8")
        status, body, _ = self.request("POST", "/api/jobs/001-a/merge", {})
        self.assertEqual((status, body.get("busy")), (409, True))


class BacklogTests(unittest.TestCase):
    TEXT = (
        "# Backlog\n\nIntro.\n\n"
        "- [x] Done → 001-done\n"
        "- [ ] First\n"
        "  Its details\n"
        "  - a point\n"
        "\n"
        "  more details\n"
        "- [ ] Second\n"
    )

    def setUp(self):
        self.repo = Repo()
        self._backlog = server.BACKLOG
        server.BACKLOG = self.repo.factory / "backlog.md"
        server.BACKLOG.write_text(self.TEXT, encoding="utf-8")

    def tearDown(self):
        server.BACKLOG = self._backlog
        self.repo.close()

    def text(self):
        return server.BACKLOG.read_text(encoding="utf-8")

    def open_items(self):
        return [l[6:] for l in self.text().splitlines() if l.startswith("- [ ] ")]

    def test_add_with_details_stores_them_indented(self):
        server.edit_backlog({"action": "add", "items": [{"text": "Third", "details": "## Why\nBecause.\n\n- x"}]})
        self.assertTrue(self.text().endswith("- [ ] Third\n  ## Why\n  Because.\n\n  - x\n"))

    def test_move_carries_the_details(self):
        server.edit_backlog({"action": "move", "index": 0, "to": 1})
        self.assertEqual(self.open_items(), ["Second", "First"])
        self.assertIn("- [ ] First\n  Its details\n  - a point\n\n  more details\n", self.text())
        self.assertIn("- [x] Done → 001-done\n- [ ] Second\n", self.text())

    def test_remove_takes_the_details_too(self):
        server.edit_backlog({"action": "remove", "index": 0})
        self.assertNotIn("Its details", self.text())
        self.assertEqual(self.open_items(), ["Second"])

    def test_the_old_one_line_add_still_works(self):
        server.edit_backlog({"action": "add", "text": "Plain"})
        self.assertEqual(self.open_items()[-1], "Plain")

    def test_bad_requests_change_nothing(self):
        for bad in [{"action": "add", "items": [{"text": "two\nlines"}]}, {"action": "add", "items": []},
                    {"action": "move", "index": 5, "to": 0}, {"action": "remove", "index": True}, {"action": "wipe"}]:
            with self.assertRaises(job.JobError, msg=bad):
                server.edit_backlog(bad)
        self.assertEqual(self.text(), self.TEXT)


if __name__ == "__main__":
    unittest.main()
