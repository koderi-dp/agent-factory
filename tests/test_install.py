"""Exercise installation into real disposable Git projects, without network access."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import install as installer
from helpers import ROOT, Repo


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(agents=False)
        self.addCleanup(self.repo.close)
        self.files = installer.payload(ROOT)

    def run_install(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            installer.install(self.repo.path, self.files, **kwargs)

    def test_fresh_install_and_repeat_preserve_project_data(self):
        target = self.repo.path
        installer.check_target(target)
        (target / ".claude/skills/custom").mkdir(parents=True)
        custom = target / ".claude/skills/custom/SKILL.md"
        custom.write_text("my skill", encoding="utf-8")
        self.run_install()
        for name in installer.REQUIRED:
            self.assertTrue((target / name).is_file(), name)
        self.assertEqual(custom.read_text(), "my skill")
        config = target / "factory/config.json"
        config.write_text('{"merge":"verify"}\n', encoding="utf-8")
        backlog = target / "factory/backlog.md"
        backlog.write_text("- [ ] Keep my feature\n", encoding="utf-8")
        state = target / "factory/jobs/001/job.json"
        state.parent.mkdir(parents=True)
        state.write_text("keep job", encoding="utf-8")
        self.run_install(force=True)
        self.assertEqual(config.read_text(), '{"merge":"verify"}\n')
        self.assertEqual(backlog.read_text(), "- [ ] Keep my feature\n")
        self.assertEqual(state.read_text(), "keep job")
        self.assertEqual((target / ".gitignore").read_text().count(installer.IGNORE), 1)

    def test_conflict_preflight_then_explicit_update(self):
        path = self.repo.path / "factory/job.py"
        path.write_text("custom version", encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "Existing files differ"):
            self.run_install()
        self.assertFalse((self.repo.path / ".claude").exists())
        self.assertEqual(path.read_text(), "custom version")
        self.run_install(force=True)
        self.assertEqual(path.read_bytes(), self.files["factory/job.py"])

    def test_dry_run_writes_nothing(self):
        before = {p.relative_to(self.repo.path): p.read_bytes()
                  for p in self.repo.path.rglob("*") if p.is_file()}
        self.run_install(dry_run=True)
        after = {p.relative_to(self.repo.path): p.read_bytes()
                 for p in self.repo.path.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_local_cli_installs_without_a_download(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "install.py"),
             "--target", str(self.repo.path), "--source", str(ROOT)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installed:", result.stdout)
        self.assertEqual((self.repo.factory / "job.py").read_bytes(), self.files["factory/job.py"])

    def test_install_in_non_git_folder_without_git_available(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            with patch.object(subprocess, "run", side_effect=AssertionError("Git must not be invoked")):
                with redirect_stdout(io.StringIO()) as output:
                    result = installer.main(["--source", str(ROOT), "--target", str(target)])
            self.assertEqual(result, 0)
            self.assertFalse((target / ".git").exists())
            for name in installer.REQUIRED:
                self.assertTrue((target / name).is_file(), name)
            self.assertIn("Before running the factory", output.getvalue())

    def test_codex_and_both_installations(self):
        for provider in ("codex", "both"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)
                with redirect_stdout(io.StringIO()):
                    result = installer.main(["--source", str(ROOT), "--target", folder, "--provider", provider])
                self.assertEqual(result, 0)
                for name in installer.required_files(provider):
                    self.assertTrue((target / name).is_file(), name)
                config = json.loads((target / "factory/config.json").read_text())
                self.assertEqual(config.get("provider", "claude"), "codex" if provider == "codex" else "claude")
                self.assertEqual((target / ".claude").exists(), provider == "both")
                result = subprocess.run([sys.executable, str(target / "factory/job.py"), "config"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("customAgents", json.loads(result.stdout))
                self.assertFalse(list((target / ".codex/agents").glob("*.toml")))

    def test_adding_codex_preserves_existing_settings(self):
        self.run_install()
        config = self.repo.factory / "config.json"
        original = config.read_bytes()
        with redirect_stdout(io.StringIO()):
            installer.install(self.repo.path, installer.payload(ROOT, provider="both"), force=True, provider="both")
        self.assertEqual(config.read_bytes(), original)
        self.assertTrue((self.repo.path / ".agents/skills/factory/SKILL.md").is_file())

    def test_ignore_append_preserves_existing_crlf(self):
        ignore = self.repo.path / ".gitignore"
        ignore.write_bytes(b"node_modules/\r\n.env")
        self.run_install()
        self.assertEqual(ignore.read_bytes(), b"node_modules/\r\n.env\r\n/factory/jobs/\r\n")

    def test_bad_target_and_parent_file_fail_without_installing(self):
        with self.assertRaisesRegex(installer.InstallError, "existing folder"):
            installer.check_target(self.repo.path / "missing")
        with self.assertRaisesRegex(installer.InstallError, "existing folder"):
            installer.check_target(self.repo.path / "app.txt")
        (self.repo.path / ".claude").write_text("not a folder", encoding="utf-8")
        with self.assertRaises(installer.InstallError):
            self.run_install(force=True)
        self.assertFalse((self.repo.factory / "job.py").exists())

    def test_symlink_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.repo.path / ".claude").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Creating symlinks requires privileges on this machine")
            with self.assertRaises(installer.InstallError):
                self.run_install(force=True)
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_download_selects_same_payload_without_runtime_state(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, content in self.files.items():
                source_name = name[1:] if name.startswith(".claude/") else name
                archive.writestr("agent-factory-main/" + source_name, content)
            archive.writestr("agent-factory-main/factory/jobs/001/job.json", "private")
            archive.writestr("agent-factory-main/claude/launch.json", "unrelated")
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(data.getvalue())) as fetch:
            with redirect_stdout(io.StringIO()):
                files = installer.payload()
        self.assertEqual(files, self.files)
        self.assertEqual(fetch.call_args.args[0], f"https://codeload.github.com/{installer.REPOSITORY}/zip/refs/heads/main")

    def test_incomplete_download_and_unsafe_paths_are_refused(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("agent-factory-main/factory/job.py", "partial")
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(data.getvalue())):
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(installer.InstallError, "Incomplete source"):
                    installer.payload()
        for name in ("factory/../../outside", "/factory/job.py", "factory\\job.py"):
            with self.assertRaises(installer.InstallError):
                installer.destination(name)


if __name__ == "__main__":
    unittest.main()
