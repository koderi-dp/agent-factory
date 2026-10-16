"""Tests for the optional uv bootstrap."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import install


class InstallTests(unittest.TestCase):

    @patch("install.subprocess.run")
    @patch("install.shutil.which", return_value="C:/uv.exe")
    def test_installs_engine_and_initializes_requested_existing_target(self, _which, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(install.main(["--target", directory]), 0)
        self.assertEqual(run.call_args_list, [
            call(["uv", "tool", "install", "agent-factory"], check=False),
            call(["factory", "init"], cwd=Path(directory).resolve(), check=False),
        ])

    @patch("install.subprocess.run")
    @patch("install.shutil.which", return_value="C:/uv.exe")
    def test_install_failure_skips_init(self, _which, run):
        run.return_value.returncode = 1
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(install.main(["--target", directory]), 1)
        run.assert_called_once_with(["uv", "tool", "install", "agent-factory"], check=False)

    @patch("builtins.input", return_value="no")
    @patch("install.shutil.which", return_value=None)
    def test_missing_uv_requires_opt_in(self, _which, _input):
        self.assertEqual(install.main([]), 1)

    @patch("install.subprocess.run")
    @patch("install.shutil.which", return_value="C:/uv.exe")
    def test_target_must_exist(self, _which, run):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            self.assertEqual(install.main(["--target", str(missing)]), 1)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
