#!/usr/bin/env python3
"""Install agent-factory into a project using only the Python standard library."""

import argparse
import io
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "koderi-dp/agent-factory"
IGNORE = "/factory/jobs/"
SEEDS = {"factory/config.json", "factory/backlog.md"}
COMMON_REQUIRED = {
    "factory/job.py",
    "factory/pipeline.py",
    "factory/server.py",
    "factory/codex_runner.py",
    "factory/dashboard/index.html",
    "factory/dashboard/dashboard.js",
    "factory/dashboard/dashboard.css",
    "factory/dashboard/sortable.min.js",
    *SEEDS,
}
ROLES = (
    "spec-writer",
    "builder",
    "reviewer-security",
    "reviewer-ux",
    "reviewer-ui",
    "reviewer-code",
    "approver",
)
CLAUDE_REQUIRED = {
    ".claude/skills/factory/references/workflow.md",
    *(f".claude/agents/{name}.md" for name in ROLES),
    *(f".claude/skills/{name}/SKILL.md"
      for name in ("factory", "factory-next", "factory-watch", "factory-dashboard")),
}
CODEX_REQUIRED = {
    *(f".codex/agents/{name}.md" for name in ROLES),
    ".agents/skills/factory/SKILL.md",
    ".agents/skills/factory/references/workflow.md",
}
REQUIRED = COMMON_REQUIRED | CLAUDE_REQUIRED


def required_files(provider):
    if provider not in {"claude", "codex", "both"}:
        raise InstallError(f"Unknown provider: {provider}")
    return COMMON_REQUIRED | (CLAUDE_REQUIRED if provider == "claude" else CODEX_REQUIRED
                              if provider == "codex" else CLAUDE_REQUIRED | CODEX_REQUIRED)


class InstallError(Exception):
    pass


def destination(name, provider="claude"):
    """Allow only shipped factory files, never runtime state or unrelated skills."""
    path = PurePosixPath(name)
    if "\\" in name or path.is_absolute() or ".." in path.parts:
        raise InstallError(f"Unsafe source path: {name}")
    if any(p in {"jobs", "__pycache__", ".git"} for p in path.parts):
        return None
    if path.suffix in {".pyc", ".tmp"} or path.name == ".lock":
        return None
    if provider in {"claude", "both"
                    } and name.startswith("claude/agents/") and path.suffix == ".md":
        return "." + name
    if provider in {"claude", "both"} and len(
            path.parts) >= 4 and path.parts[:2] == ("claude", "skills"):
        if path.parts[2] in {"factory", "factory-next", "factory-watch", "factory-dashboard"}:
            return "." + name
    if provider in {"codex", "both"}:
        if name.startswith("codex/agents/") and path.suffix == ".md":
            return "." + name
        if name.startswith("codex/skills/factory/"):
            return name.replace("codex/skills/", ".agents/skills/", 1)
    if name in {
            "factory/job.py", "factory/pipeline.py", "factory/server.py", "factory/codex_runner.py",
            "factory/README.md", *SEEDS
    }:
        return name
    if name.startswith("factory/dashboard/"):
        return name
    return None


def payload(source=None, repository=REPOSITORY, provider="claude"):
    required = required_files(provider)
    files = {}
    if source is not None:
        for folder in (source / "claude", source / "codex", source / "factory"):
            for path in folder.rglob("*"):
                name = destination(path.relative_to(source).as_posix(), provider)
                if name and path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                        raise InstallError(f"Source file escapes source folder: {path}")
                    files[name] = path.read_bytes()
    else:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise InstallError("--repo must be a GitHub owner/repository")
        url = f"https://codeload.github.com/{repository}/zip/refs/heads/main"
        print(f"Downloading {repository}@main ...")
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        # Read selected members directly; never extract archive paths to disk.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            roots = {p.filename.split("/", 1)[0] for p in archive.infolist()}
            if len(roots) != 1:
                raise InstallError("Expected one top-level folder in the download")
            for member in archive.infolist():
                if member.is_dir():
                    continue
                name = destination(member.filename.split("/", 1)[-1], provider)
                if name:
                    if name in files:
                        raise InstallError(f"Duplicate source file: {name}")
                    files[name] = archive.read(member)
    missing = required - files.keys()
    if missing:
        raise InstallError("Incomplete source; missing: " + ", ".join(sorted(missing)))
    return files


def check_target(target):
    target = target.resolve()
    if not target.is_dir():
        raise InstallError(f"Target must be an existing folder: {target}")


def checked_path(target, name):
    path = target / name
    if not path.resolve().is_relative_to(target):
        raise InstallError(f"Destination escapes project folder: {path}")
    for parent in (path, *path.parents):
        if parent == target:
            break
        if parent.is_symlink():
            raise InstallError(f"Refusing to install through a symlink: {parent}")
        if parent != path and parent.exists() and not parent.is_dir():
            raise InstallError(f"Expected a folder: {parent}")
    if path.exists() and not path.is_file():
        raise InstallError(f"Expected a file: {path}")
    return path


def install(target, files, force=False, dry_run=False, provider="claude"):
    """Preflight every destination before writing anything."""
    target = target.resolve()
    writes, conflicts = {}, []
    preserved = 0
    for name, content in sorted(files.items()):
        path = checked_path(target, name)
        if path.exists():
            if name in SEEDS:
                preserved += 1
                continue
            if path.read_bytes() == content:
                continue
            if not force:
                conflicts.append(name)
                continue
        if name == "factory/config.json" and provider == "codex":
            config = json.loads(content)
            config["provider"] = "codex"
            content = (json.dumps(config, indent=2) + "\n").encode("utf-8")
        writes[path] = content
    ignore = checked_path(target, ".gitignore")
    content = ignore.read_bytes() if ignore.exists() else b""
    if IGNORE.encode() not in content.splitlines():
        newline = b"\r\n" if b"\r\n" in content else b"\n"
        writes[ignore] = content + (newline if content and not content.endswith(b"\n") else
                                    b"") + IGNORE.encode() + newline
    if conflicts:
        raise InstallError(
            "Existing files differ (no files changed):\n  " + "\n  ".join(conflicts) +
            "\nReview them, then use --force to replace factory files. Settings and backlog are always preserved."
        )
    for path, content in writes.items():
        print(f"{'Would write' if dry_run else 'Write'} {path.relative_to(target).as_posix()}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(
        f"{'Dry run' if dry_run else 'Installed'}: {len(writes)} file(s), {preserved} existing settings/backlog file(s) preserved."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target",
                        type=Path,
                        default=Path.cwd(),
                        help="Existing project folder (default: current folder)")
    parser.add_argument("--provider",
                        choices=("claude", "codex", "both"),
                        default="claude",
                        help="Client support to install (default: claude)")
    parser.add_argument("--repo", default=REPOSITORY, help="GitHub owner/repository")
    parser.add_argument("--source",
                        type=Path,
                        help="Install from a local agent-factory checkout without downloading")
    parser.add_argument("--force",
                        action="store_true",
                        help="Replace differing factory files; preserve settings and backlog")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing files")
    args = parser.parse_args(argv)
    try:
        target = args.target.expanduser().resolve()
        check_target(target)
        files = payload(args.source.expanduser().resolve() if args.source else None, args.repo,
                        args.provider)
        install(target, files, args.force, args.dry_run, args.provider)
    except (InstallError, OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        clients = ("claude", "codex") if args.provider == "both" else (args.provider, )
        for executable in (*clients, "agent-browser"):
            if not shutil.which(executable):
                print(f"Missing runtime tool: {executable} (see README requirements).")
        print("Next: describe setup, dev server ports and validation in AGENTS.md or CLAUDE.md.")
        print(
            "Before running the factory, make this folder a Git repository with a local main branch and at least one commit."
        )
        print("Commit the installed agent/skill files, factory/ and .gitignore.")
        if args.provider in {"codex", "both"}:
            print("Open Codex in your project and run $factory <feature>.")
        if args.provider in {"claude", "both"}:
            print("Open Claude Code in your project and run /factory <feature>.")
        print("Dashboard Settings selects the client for Start; existing settings are preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
