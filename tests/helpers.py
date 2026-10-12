"""A throwaway git repo with a factory/ folder, and job.py pointed at it."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "factory"))

import job  # noqa: E402  (needs the path above)


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


class Repo:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-test-"))
        self.path = self.tmp / "app"
        self.factory = self.path / "factory"
        (self.factory / "jobs").mkdir(parents=True)
        (self.path / "app.txt").write_text("one\n", encoding="utf-8")
        (self.path / ".gitignore").write_text("/factory/jobs/\n", encoding="utf-8")
        (self.factory / "backlog.md").write_text("# Backlog\n", encoding="utf-8")
        git("init", "-q", "-b", "main", cwd=self.path)
        git("config", "user.name", "test", cwd=self.path)
        git("config", "user.email", "test@localhost", cwd=self.path)
        git("config", "core.autocrlf", "false", cwd=self.path)
        git("add", "-A", cwd=self.path)
        git("commit", "-q", "-m", "start", cwd=self.path)
        self._saved = (job.FACTORY, job.JOBS, job.CONFIG)
        job.FACTORY, job.JOBS, job.CONFIG = self.factory, self.factory / "jobs", self.factory / "config.json"

    def add_job(self, job_id, stage="approved", file="app.txt", line="from the job\n"):
        """A job with its own worktree and one commit on its branch."""
        worktree = self.tmp / "app-factory" / job_id
        (job.JOBS / job_id).mkdir()
        job.new(job_id, f"feature {job_id}", str(worktree))
        git("worktree", "add", "-q", "-b", f"factory/{job_id}", str(worktree), "main", cwd=self.path)
        with open(worktree / file, "a", encoding="utf-8") as f:
            f.write(line)
        git("add", file, cwd=worktree)
        git("commit", "-q", "-m", f"change for {job_id}", cwd=worktree)
        job.update(job_id, [("stage", stage)])
        return worktree

    def close(self):
        job.FACTORY, job.JOBS, job.CONFIG = self._saved
        for line in git("worktree", "list", "--porcelain", cwd=self.path).splitlines():
            if line.startswith("worktree ") and Path(line[9:]) != self.path:
                subprocess.run(["git", "worktree", "remove", "--force", line[9:]], cwd=self.path, capture_output=True)
        remove_tree(self.tmp)


def remove_tree(path):
    """Delete a folder, including git's read-only object files, which Windows won't delete as they are."""
    def unlock(func, p, _):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=unlock)
    else:
        shutil.rmtree(path, onerror=unlock)
