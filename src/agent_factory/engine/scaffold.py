"""Idempotent repository setup and reviewable upgrades (§12.2, §15.6)."""

import json
import difflib
import hashlib
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


def check_init(root: Path) -> Path:
    """Validate before asking for a client or writing any files."""
    root = Path(gitops.checked("rev-parse", "--show-toplevel", cwd=root)).resolve()
    if any((root / name).exists()
           for name in ("factory/job.py", "factory/server.py", "factory/dashboard")):
        raise ValueError("This is a v1 install; run `factory upgrade` (plan §15.6)")
    existing = root / "factory/config.json"
    if existing.exists():
        if json.loads(existing.read_text(encoding="utf-8-sig")).get("version", 1) != 2:
            raise ValueError("This is a v1 config; run `factory upgrade` (plan §15.6)")
        versioning.check(root)
    if not gitops.branch_exists(root, "main") or gitops.git("rev-parse", "HEAD",
                                                            cwd=root).returncode:
        raise ValueError("factory init needs a Git repo with a main branch and at least one commit")
    if not any((root / name).is_file() for name in ("AGENTS.md", "CLAUDE.md")):
        raise ValueError(
            "Add AGENTS.md or CLAUDE.md with project setup and check instructions first")
    return root


def init(root: Path, provider: str, *, dry_run: bool = False) -> list[str]:
    root = check_init(root)
    if provider not in {"claude", "codex", "opencode", "both"}:
        raise ValueError("Client must be claude, codex, opencode or both")
    raw = template_config()
    raw["engine"] = engine_range()
    if provider != "both":
        for steps in raw["pipelines"].values():
            for step in steps:
                for execution in step.get("parallel", [step]):
                    execution["provider"] = provider
    files = {
        "factory/config.json": json.dumps(raw, indent=2) + "\n",
        "factory/backlog.md": (TEMPLATES / "backlog.md").read_text(encoding="utf-8")
    }
    for client, path in CLIENT_SKILLS.items():
        # "both" stays claude + codex; opencode is opted into explicitly.
        if provider == client or (provider == "both" and client in {"claude", "codex"}):
            files[path] = (TEMPLATES / f"SKILL.{client}.md").read_text(encoding="utf-8")
    messages = []
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
        checks.append({
            "check": name,
            "ok": bool(path and Path(path).is_file()),
            "detail": path or "not found on PATH"
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
        checks.append({"check": "git repository", "ok": result.returncode == 0})
    # CLI login stores differ by provider/version; inspect presence, never print secrets.
    home = Path.home()
    import os
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    for name, candidates in (("codex login", [codex_home / "auth.json"]),
                             ("claude login", [home / ".claude/.credentials.json"]),
                             ("opencode login", [home / ".local/share/opencode/auth.json"])):
        present = any(path.is_file() for path in candidates)
        checks.append({
            "check":
            name,
            "ok":
            present,
            "detail":
            "saved credentials found (validity checked on use)"
            if present else "no credential file found; run the provider's login command"
        })
    start, end = map(int, settings.ports.range.split("-"))
    occupied = ports.listeners(range(start, end + 1))
    checks.append({
        "check": "ports",
        "ok": len({p.port
                   for p in occupied}) < end - start + 1,
        "detail": f"{settings.ports.range}: {len(occupied)} listeners"
    })
    return checks
