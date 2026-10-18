"""Idempotent repository setup and reviewable upgrades (§12.2, §15.6)."""

import json
import difflib
import hashlib
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from agent_factory import run_cli
from agent_factory.engine import gitops, registry, versioning
from agent_factory.engine.config import engine_range, template_config, upgrade_v1, validate_config

TEMPLATES = Path(__file__).parent.parent / "templates"

# OpenCode V2 discovers skills in .opencode/skills/<id>/SKILL.md.
CLIENT_SKILLS = {
    "claude": ".claude/skills/factory/SKILL.md",
    "codex": ".agents/skills/factory/SKILL.md",
    "opencode": ".opencode/skills/factory/SKILL.md",
}

PROVIDERS = ("claude", "codex", "opencode")
TOOL_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def credential_files(name: str) -> list[Path]:
    """Where each tool keeps its saved sign-in. Only their presence is ever checked."""
    home = Path.home()
    if name == "codex":
        return [Path(os.environ.get("CODEX_HOME", home / ".codex")) / "auth.json"]
    if name == "claude":
        return [home / ".claude/.credentials.json"]
    return [home / ".local/share/opencode/auth.json"]


@dataclass(frozen=True)
class Tool:
    name: str
    installed: bool
    signed_in: bool


def detect_tools(overrides: Mapping[str, str | None] | None = None) -> dict[str, Tool]:
    """Which of the supported tools are installed, and have saved credentials.

    `overrides` are machine-setting paths (`cli.<tool>`) that win over PATH.
    """
    overrides = overrides or {}
    tools = {}
    for name in PROVIDERS:
        path = overrides.get(name) or shutil.which(name)
        installed = bool(path and Path(path).is_file())
        saved = any(file.is_file() for file in credential_files(name))
        tools[name] = Tool(name, installed, installed and saved)
    return tools


@dataclass(frozen=True)
class ProviderChoice:
    provider: str | None  # the default to use; None when no tool is installed
    options: tuple[str, ...]  # what to ask the user to pick from; empty when nothing to ask
    skills: tuple[str, ...]  # in-chat shortcuts to install: every tool that was found
    note: str
    available: tuple[str, ...] = ()  # tools a stage may run on


def choose_provider(tools: Mapping[str, Tool]) -> ProviderChoice:
    """Pick the pipeline's starting tool from what is on this machine.

    One usable tool needs no question. Several do, and then "both" (a mix) is offered
    for any combination of the three tools.
    """
    installed = [name for name in PROVIDERS if tools[name].installed]
    if not installed:
        return ProviderChoice(
            None, (), (), "No supported tool found. Install Claude Code, Codex or OpenCode and "
            "sign in, then run `factory init` again. `factory doctor` shows what is missing.")
    ready = [name for name in installed if tools[name].signed_in]
    candidates = ready or installed
    names = [TOOL_NAMES[name] for name in candidates]
    # Some tools keep their sign-in outside any file we can see, so an unconfirmed
    # sign-in is a warning, never a reason to stop.
    warning = ("" if ready else f" Could not confirm you are signed in to {', '.join(names)}; "
               "if jobs pause, sign in and resume them.")
    skills = tuple(installed)
    available = tuple(candidates)
    if len(candidates) == 1:
        sole = "the only signed-in tool" if ready else "the only tool"
        return ProviderChoice(candidates[0], (), skills,
                              f"Using {names[0]} for every stage: it is {sole} found.{warning}",
                              available)
    return ProviderChoice(candidates[0], available + ("both", ), skills,
                          f"Found {', '.join(names[:-1])} and {names[-1]}.{warning}", available)


def upgrade(root: Path, *, dry_run: bool = False) -> str:
    """Plan every file change before writing; dry-run has no registry/service side effects."""
    root = root.resolve()
    raw = json.loads((root / "factory/config.json").read_text(encoding="utf-8-sig"))
    converted = upgrade_v1(raw)
    converted["engine"] = engine_range()
    writes = {"factory/config.json": (json.dumps(converted, indent=2) + "\n").encode()}
    deletes: set[str] = set()
    clients = set()
    hashes = json.loads((TEMPLATES / "v1-role-hashes.json").read_text(encoding="utf-8"))
    configured_roles = {
        entry["uses"]
        for steps in converted["pipelines"].values()
        for step in steps
        for entry in step.get("parallel", [step])
    }
    for client, directory in (("claude", ".claude"), ("codex", ".codex")):
        for source in sorted((root / directory / "agents").glob("*.md")):
            name = source.stem
            original = hashes[client].get(name)
            if original is None and name not in configured_roles:
                continue
            if not source.resolve().is_relative_to(root):
                raise ValueError(f"Upgrade path escapes repository: {source}")
            clients.add(client)
            data = source.read_bytes()
            if hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() != original:
                target = f"factory/roles/{name}.{client}.md"
                if (root / target).exists() and (root / target).read_bytes() != data:
                    raise ValueError(
                        f"Custom role conflict: {target}; preserve both files manually")
                writes[target] = data
            deletes.add(source.relative_to(root).as_posix())
    for steps in converted["pipelines"].values():
        for step in steps:
            clients.update(e["provider"] for e in step.get("parallel", [step]))
    for directory in (".claude", ".agents"):
        if (root / directory / "skills/factory").exists():
            clients.add("claude" if directory == ".claude" else "codex")
    if raw.get("version", 1) != 2 or any(
        (root / name).exists()
            for name in ("factory/job.py", "factory/server.py", "factory/dashboard")):
        for path in (root / "factory").glob("*.py"):
            deletes.add(path.relative_to(root).as_posix())
    trees = ["factory/dashboard", ".agents/skills/factory"]
    trees.extend(f".claude/skills/{name}"
                 for name in ("factory", "factory-next", "factory-watch", "factory-dashboard"))
    for name in trees:
        directory = root / name
        if not directory.resolve().is_relative_to(root):
            raise ValueError(f"Upgrade path escapes repository: {name}")
        if directory.exists():
            deletes.update(
                p.relative_to(root).as_posix() for p in directory.rglob("*") if p.is_file())
    for client in sorted(clients):
        path = CLIENT_SKILLS.get(client)
        if path is not None:
            writes[path] = (TEMPLATES / f"SKILL.{client}.md").read_bytes()
    ignore = root / ".gitignore"
    content = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    missing = [
        line for line in ("/factory/state/", "/factory/jobs/") if line not in content.splitlines()
    ]
    if missing:
        writes[".gitignore"] = (content + ("\n" if content and not content.endswith("\n") else "") +
                                "\n".join(missing) + "\n").encode()
    changes = []
    for name in sorted(deletes | writes.keys()):
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Upgrade path escapes repository: {name}")
        old = path.read_bytes() if path.is_file() else b""
        new = writes.get(name, b"")
        if old != new or (name in deletes and name not in writes):
            changes.append((name, old, new))
    diff = "".join("".join(
        difflib.unified_diff(old.decode("utf-8-sig", errors="replace").splitlines(keepends=True),
                             new.decode("utf-8-sig", errors="replace").splitlines(keepends=True),
                             fromfile=f"a/{name}" if old else "/dev/null",
                             tofile=f"b/{name}" if name in writes else "/dev/null"))
                   for name, old, new in changes)
    # Validate the planned roles too, without modifying repository or machine state.
    with tempfile.TemporaryDirectory(prefix="factory-upgrade-") as temporary:
        staged = Path(temporary)
        current_roles = root / "factory/roles"
        if current_roles.exists():
            shutil.copytree(current_roles, staged / "factory/roles")
        for name, data in writes.items():
            if name.startswith("factory/roles/"):
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        validate_config(converted, root=staged)
    if dry_run:
        return diff or "Already up to date."
    # Persist customised roles before removing their v1 sources, including after a crash.
    for name, _, new in sorted(changes, key=lambda change: change[0] not in writes):
        path = root / name
        if name in writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            run_cli.atomic_write(path, new)
        else:
            path.unlink()
    # Remove only empty directories, including the v1 marker checked by init.
    for name in trees:
        directory = root / name
        if directory.is_dir():
            for path in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                directory.rmdir()
            except OSError:
                pass
    connection = registry.open_registry()
    try:
        registry.register(connection, root)
    finally:
        connection.close()
    return "\n".join(f"{'wrote' if name in writes else 'removed'}: {name}"
                     for name, _, _ in changes) or "Already up to date."


@dataclass(frozen=True)
class Setup:
    """What `init` found in the folder, read without writing anything."""
    root: Path
    repo: bool
    branch: str | None
    commits: bool
    doc: str | None


def _toplevel(root: Path) -> Path | None:
    result = gitops.git("rev-parse", "--show-toplevel", cwd=root)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def _identity_configured(root: Path) -> bool:
    return all(
        gitops.git("config", "--get", key, cwd=root).returncode == 0
        for key in ("user.name", "user.email"))


def inspect(root: Path) -> Setup:
    """Validate the folder and report its Git and doc state; never writes."""
    root = root.resolve()
    if any((root / name).exists()
           for name in ("factory/job.py", "factory/server.py", "factory/dashboard")):
        raise ValueError("This is a v1 install; run `factory upgrade` (plan §15.6)")
    existing = root / "factory/config.json"
    if existing.exists():
        if json.loads(existing.read_text(encoding="utf-8-sig")).get("version", 1) != 2:
            raise ValueError("This is a v1 config; run `factory upgrade` (plan §15.6)")
        versioning.check(root)
    top = _toplevel(root)
    repo = top is not None
    if repo and top != root:
        raise ValueError(f"This folder is inside another Git repository at {top}. Run "
                         "`factory init` from that folder, or start a separate repo here with "
                         "`git init -b main`.")
    branch = None
    commits = False
    if repo:
        branch = gitops.git("symbolic-ref", "--short", "-q", "HEAD",
                            cwd=root).stdout.strip() or None
        commits = gitops.git("rev-parse", "--verify", "-q", "HEAD", cwd=root).returncode == 0
        if branch is None:
            raise ValueError("HEAD is detached. Switch to a branch first, for example "
                             "`git switch -c main`, then run `factory init` again.")
    doc = next((name for name in ("AGENTS.md", "CLAUDE.md") if (root / name).is_file()), None)
    if (not repo or not commits) and not _identity_configured(root):
        raise ValueError("Git needs your name and email for the first commit. Run:\n"
                         '  git config --global user.name "Your Name"\n'
                         '  git config --global user.email "you@example.com"\n'
                         "then run `factory init` again.")
    return Setup(root=root, repo=repo, branch=branch, commits=commits, doc=doc)


def check_init(root: Path) -> Path:
    """Validate before asking for a client or writing any files."""
    return inspect(root).root


# Claude Code reads AGENTS.md itself only on recent versions, and only when there is no
# CLAUDE.md. A CLAUDE.md that imports it works on every version and is never read twice.
CLAUDE_IMPORT = ("# Project instructions\n\n"
                 "The instructions for every coding tool live in AGENTS.md.\n\n"
                 "@AGENTS.md\n")


def starter_docs(root: Path, tools: Collection[str]) -> list[tuple[str, str]]:
    """The starter instructions files to create, as (name, content).

    Claude Code alone gets CLAUDE.md. Any other tool reads AGENTS.md, so when Claude Code
    shares the project with one, CLAUDE.md imports AGENTS.md (the layout Claude Code's own
    docs recommend) instead of holding a second copy that would drift.
    """
    commands = {key: "TODO" for key in ("install", "dev", "test", "build", "lint")}
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        package = None
    if isinstance(package, dict):
        scripts = package.get("scripts", {})
        commands["install"] = "npm install"
        for key in ("dev", "test", "build", "lint"):
            if key in scripts:
                commands[key] = f"npm run {key}"
    text = (TEMPLATES / "AGENTS.starter.md").read_text(encoding="utf-8")
    for key, value in commands.items():
        text = text.replace("{" + key + "}", value)
    if set(tools) == {"claude"}:
        return [("CLAUDE.md", text)]
    docs = [("AGENTS.md", text)]
    if "claude" in tools:
        docs.append(("CLAUDE.md", CLAUDE_IMPORT))
    return docs


def init(root: Path,
         provider: str,
         *,
         dry_run: bool = False,
         skills: Sequence[str] | None = None,
         available: Sequence[str] | None = None) -> list[str]:
    """Create what is missing, or describe it when dry_run is set.

    Dry runs take the same branches as real runs, so the plan shown to the user is
    the one that would execute.
    """
    setup = inspect(root)
    root = setup.root
    if provider not in {"claude", "codex", "opencode", "both"}:
        raise ValueError("Client must be claude, codex, opencode or both")
    word = "would " if dry_run else ""
    messages = []
    base = setup.branch or "main"
    if not setup.repo:
        messages.append(f"{'would create' if dry_run else 'created'}: git repository on {base}")
    # By default "both" stays claude + codex and opencode is opted into explicitly;
    # a caller that detected the installed tools passes exactly the ones to install.
    wanted = set(skills) if skills is not None else (
        {"claude", "codex"} if provider == "both" else {provider})
    docs = starter_docs(root, wanted) if setup.doc is None else []
    committed = [name for name, _ in docs] or [setup.doc]
    for number, (name, _) in enumerate(docs):
        note = "starter; edit the TODO lines" if number == 0 else "imports AGENTS.md for Claude Code"
        messages.append(f"{'would write' if dry_run else 'wrote'}: {name} ({note})")
    raw = template_config()
    raw["engine"] = engine_range()
    raw["baseBranch"] = base
    for steps in raw["pipelines"].values():
        for step in steps:
            for execution in step.get("parallel", [step]):
                if provider != "both":
                    execution["provider"] = provider
                elif available and execution["provider"] not in available:
                    # A mix keeps the template's tool per stage, and moves a stage whose
                    # tool is not here to the first tool that is.
                    execution["provider"] = available[0]
    files = {
        "factory/config.json": json.dumps(raw, indent=2) + "\n",
        "factory/backlog.md": (TEMPLATES / "backlog.md").read_text(encoding="utf-8")
    }
    for client, path in CLIENT_SKILLS.items():
        if client in wanted:
            files[path] = (TEMPLATES / f"SKILL.{client}.md").read_text(encoding="utf-8")
    needs_commit = not setup.repo or not setup.commits
    if needs_commit:
        messages.append(f"{'would commit' if dry_run else 'committed'}: {', '.join(committed)} "
                        f"as the first commit on {base}")
    if not dry_run:
        if not setup.repo:
            gitops.checked("init", "-q", "-b", base, cwd=root)
        for name, text in docs:
            (root / name).write_text(text, encoding="utf-8")
        if needs_commit:
            gitops.checked("add", "--", *committed, cwd=root)
            gitops.checked("commit", "-q", "-m", "Initial commit: agent-factory setup", cwd=root)
    for name, content in files.items():
        target = root / name
        if target.exists():
            messages.append(f"unchanged (exists): {name}")
        else:
            messages.append(f"{'would write' if dry_run else 'wrote'}: {name}")
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.encode("utf-8"))
    ignore = root / ".gitignore"
    content = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    missing = [
        line for line in ("/factory/state/", "/factory/jobs/") if line not in content.splitlines()
    ]
    if missing:
        messages.append(f"{'would update' if dry_run else 'updated'}: .gitignore")
        if not dry_run:
            ignore.write_bytes((content + ("\n" if content and not content.endswith("\n") else "") +
                                "\n".join(missing) + "\n").encode("utf-8"))
    if not dry_run:
        connection = registry.open_registry()
        try:
            registry.register(connection, root)
        finally:
            connection.close()
    return messages


def doctor(root: Path | None = None) -> list[dict[str, Any]]:
    """Check tools and persist discovered provider paths without invoking providers."""
    from agent_factory.engine.config import load_machine_settings
    from agent_factory.engine import db, ports
    connection = registry.open_registry()
    try:
        settings = load_machine_settings(connection)
    finally:
        connection.close()
    checks = []
    discoveries = {}
    for name in ("git", "codex", "claude", "opencode", "agent-browser", "agent-factory"):
        override = getattr(settings.cli, name, None)
        path = override or shutil.which(name)
        if (name in {"codex", "claude", "opencode"} and not override and path
                and Path(path).is_file()):
            discoveries[name] = str(Path(path).resolve())
        ok = bool(path and Path(path).is_file())
        checks.append({
            "check": name,
            "ok": ok,
            "detail": path or "not found on PATH",
            "required": name == "git",
            "fix": None if ok else _TOOL_FIXES[name]
        })
    if discoveries:
        connection = registry.open_registry()
        try:
            with db.tx(connection):
                for name, path in discoveries.items():
                    connection.execute(
                        "INSERT INTO settings (key,value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                        "WHERE settings.value='null'", (f"cli.{name}", json.dumps(path)))
        finally:
            connection.close()
    if root is not None and shutil.which("git"):
        result = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True)
        ok = result.returncode == 0
        checks.append({
            "check": "git repository",
            "ok": ok,
            "detail": "ok" if ok else str(root),
            "required": False,
            "fix": None if ok else "Run `factory init` in the project folder."
        })
    # CLI login stores differ by provider/version; inspect presence, never print secrets.
    for name, fix in (
        ("codex", "Run `codex login`."),
        ("claude", "Run `claude` and sign in."),
        ("opencode", "Sign in to OpenCode, then run this check again."),
    ):
        candidates = credential_files(name)
        present = any(path.is_file() for path in candidates)
        checks.append({
            "check": f"{name} login",
            "ok": present,
            "detail": "saved credentials found (validity checked on use)"
            if present else "no credential file found",
            "required": False,
            "fix": None if present else fix
        })
    start, end = map(int, settings.ports.range.split("-"))
    occupied = ports.listeners(range(start, end + 1))
    free = end - start + 1 - len({p.port for p in occupied})
    checks.append({
        "check":
        "ports",
        "ok":
        free > 0,
        "detail":
        f"{settings.ports.range}: {len(occupied)} listeners",
        "required":
        True,
        "fix":
        None if free > 0 else "Stop a process using a port in that range, or widen it: "
        "factory config --machine ports.range=5000-5999"
    })
    dashboard_busy = bool(ports.listeners(range(DASHBOARD_PORT, DASHBOARD_PORT + 1)))
    checks.append({
        "check":
        "dashboard port",
        "ok":
        not dashboard_busy,
        "detail":
        f"127.0.0.1:{DASHBOARD_PORT} " + ("in use" if dashboard_busy else "free"),
        "required":
        False,
        "fix":
        None if not dashboard_busy else
        "Something else uses this port. Start the dashboard elsewhere: factory serve --port 5199"
    })
    return checks


DASHBOARD_PORT = 5099
_TOOL_FIXES = {
    "git": "Install Git (https://git-scm.com), then open a new terminal.",
    "codex": "Install the Codex CLI, or set its path: factory config --machine cli.codex=<path>",
    "claude": "Install the Claude Code CLI, or set its path: "
    "factory config --machine cli.claude=<path>",
    "opencode": "Install OpenCode, or set its path: factory config --machine cli.opencode=<path>",
    "agent-browser": "Optional, for browser checks: npm install -g agent-browser && "
    "agent-browser install",
    "agent-factory": "Reinstall the engine: uv tool install agent-factory-engine",
}
