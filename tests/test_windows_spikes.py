"""Session 0.3 evidence checks and disposable Windows process integration probes."""

import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import tempfile
import unittest
from unittest.mock import patch

from spikes.windows import process_spikes

FIXTURES = Path(__file__).parent / "fixtures" / "windows"


class WindowsSpikeEvidenceTests(unittest.TestCase):

    def test_tree_evidence_covers_detachment_and_all_three_stop_paths(self):
        evidence = json.loads((FIXTURES / "tree.json").read_text(encoding="utf-8"))
        self.assertEqual({case["mode"]
                          for case in evidence["cases"]}, {"terminate", "stop", "close"})
        for case in evidence["cases"]:
            with self.subTest(mode=case["mode"]):
                launcher = case["launcher"]
                launched = json.loads(launcher["stdout"])
                self.assertEqual(launcher["exit_code"], 0)
                self.assertEqual(launched["creationflags"], 0x08000000 | 0x00000200)
                pids = {member["pid"] for member in case["members"]}
                self.assertIn(launched["wrapper_pid"], pids)
                self.assertIn(case["wrapper"]["npm_pid"], pids)
                self.assertIn(case["server"]["pid"], pids)
                self.assertNotIn(launched["launcher_pid"], pids)
                self.assertGreaterEqual(
                    sum(
                        PureWindowsPath(member["image"]).name.lower() == "node.exe"
                        for member in case["members"]), 2)
                self.assertFalse(case["wrapper"]["handle_inheritable"])
                self.assertTrue(case["launcher_exited_tree_survived"])
                self.assertEqual(case["http_after_launcher_exit"], "200 OK")
                self.assertTrue(case["all_members_exited"])
                self.assertTrue(case["port_rebound"])

    def test_uv_evidence_shows_new_version_and_same_base_worker(self):
        case = json.loads((FIXTURES / "uv.json").read_text(encoding="utf-8"))["case"]
        for name in ("install", "launch", "force_install", "new_version"):
            self.assertEqual(case[name]["exit_code"], 0, name)
        self.assertIn("--force", case["force_install"]["argv"])
        self.assertIn("factory-windows-spike==0.0.2", case["force_install"]["stderr"])
        self.assertEqual(case["new_version"]["stdout"].strip(), "0.0.2")
        self.assertEqual(json.loads(case["launch"]["stdout"])["version"], "0.0.1")
        self.assertEqual(case["before"]["pid"], case["after"]["pid"])
        self.assertGreater(case["after"]["count"], case["before"]["count"])
        self.assertEqual(case["worker_image"], case["launched"]["base_executable"])
        self.assertNotEqual(case["worker_image"], case["launched"]["tool_executable"])
        self.assertEqual(case["after"]["script"], "<SCRATCH>\\uv\\runtime\\base_worker.py")
        self.assertTrue(case["same_worker_alive"])
        self.assertTrue(case["no_file_lock_failure"])
        self.assertTrue(case["worker_stopped"])

    def test_redaction_preserves_nested_json_and_resolves_temp_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            for path in (str(scratch), str(scratch.resolve()), scratch.as_posix()):
                for depth in range(4):
                    text = path + "/script.py"
                    for _ in range(depth):
                        text = json.dumps(text)
                    text = process_spikes.redact(text, scratch)
                    self.assertNotIn(scratch.name, text)
                    self.assertIn("<SCRATCH>", text)
                    for _ in range(depth):
                        text = json.loads(text)

    def test_existing_evidence_prevents_any_live_experiment(self):
        with patch("sys.argv", ["process_spikes.py", "all"]), \
                patch.object(process_spikes, "tree_probe") as tree, \
                patch.object(process_spikes, "uv_probe") as uv, \
                patch.object(process_spikes.os, "name", "nt"):
            with self.assertRaises(FileExistsError):
                process_spikes.main()
            tree.assert_not_called()
            uv.assert_not_called()

    def test_transient_windows_sharing_violation_does_not_abort_json_exchange(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            original_replace = os.replace
            failures = [True]

            def replace(source: Path, destination: Path) -> None:
                if failures:
                    failures.pop()
                    raise PermissionError("transient sharing violation")
                original_replace(source, destination)

            with patch.object(process_spikes.os, "replace", side_effect=replace):
                process_spikes.write_json(path, {"ready": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ready": True})
            with patch.object(
                    Path,
                    "read_text",
                    side_effect=[PermissionError("transient sharing violation"),
                                 '{"ready": true}']):
                self.assertEqual(process_spikes.read_json(path), {"ready": True})


@unittest.skipUnless(os.name == "nt", "Job Objects require Windows")
class WindowsSpikeLiveTests(unittest.TestCase):

    @unittest.skipUnless(shutil.which("npm.cmd"), "npm.cmd is required")
    def test_real_npm_tree_survives_launcher_and_all_stop_paths_release_port(self):
        # Space/non-ASCII paths exercise Windows argv and path handling too.
        with tempfile.TemporaryDirectory(prefix="factory spike č ") as temp:
            for mode in ("terminate", "stop", "close"):
                with self.subTest(mode=mode):
                    process_spikes.tree_probe(Path(temp) / mode, mode)

    @unittest.skipUnless(shutil.which("uv"), "uv is required")
    def test_force_install_during_base_worker(self):
        with tempfile.TemporaryDirectory(prefix="factory uv spike č ") as temp:
            process_spikes.uv_probe(Path(temp) / "uv")


if __name__ == "__main__":
    unittest.main()
