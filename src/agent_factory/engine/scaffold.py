"""Idempotent repository setup; upgrades/eject belong to sessions 4.2/2.2."""

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from agent_factory import __version__
from agent_factory.engine import gitops, registry, versioning
from agent_factory.engine.config import template_config

TEMPLATES = Path(__file__).parent.parent / "templates"


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
    if provider not in {"claude", "codex", "both"}:
        raise ValueError("Client must be claude, codex or both")
    raw = template_config()
    major = int(__version__.split(".")[0])
    raw["engine"] = f">={major}.0,<{major + 1}"
    if provider != "both":
        for steps in raw["pipelines"].values():
            for step in steps:
                for execution in step.get("parallel", [step]):
                    execution["provider"] = provider
    files = {
        "factory/config.json": json.dumps(raw, indent=2) + "\n",
        "factory/backlog.md": (TEMPLATES / "backlog.md").read_text(encoding="utf-8")
    }
    for client, directory in (("claude", ".claude"), ("codex", ".agents")):
        if provider in {client, "both"}:
            files[f"{directory}/skills/factory/SKILL.md"] = (TEMPLATES /
                                                             f"SKILL.{client}.md").read_text(
                                                                 encoding="utf-8")
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
    for name in ("git", "codex", "claude", "agent-browser", "agent-factory"):
        override = getattr(settings.cli, name, None)
        path = override or shutil.which(name)
        if name in {"codex", "claude"} and not override and path and Path(path).is_file():
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
                             ("claude login", [home / ".claude/.credentials.json"])):
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
