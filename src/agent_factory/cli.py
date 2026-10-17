"""Local setup and the authenticated service-backed factory commands (§12)."""

import argparse
import json
from pathlib import Path
import sys
import sqlite3
import time
from urllib.parse import quote
import webbrowser

from agent_factory import __version__
from agent_factory import service
from agent_factory.engine import registry, roles, scaffold, versioning
from agent_factory.paths import find_repo, machine_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo",
                        help="Repository path or registered id",
                        default=argparse.SUPPRESS)
    parser.add_argument("--repo", help="Repository path or registered id")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, section: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common])
        sub.set_defaults(section=section)
        return sub

    init = command("init", "12.2")
    init.add_argument("--provider", choices=["claude", "codex", "opencode", "both"])
    init.add_argument("--dry-run", action="store_true")
    command("upgrade", "15.4").add_argument("--dry-run", action="store_true")
    eject = command("eject", "5.3")
    eject.add_argument("role")
    eject.add_argument("--provider", choices=["claude", "codex", "opencode"])
    eject.add_argument("--diff", action="store_true")
    command("doctor", "12.1")
    repos = command("repos", "4.3").add_subparsers(dest="repos_action")
    repos.add_parser("list")
    repos.add_parser("remove").add_argument("id")
    rename = repos.add_parser("rename")
    rename.add_argument("id")
    rename.add_argument("name")
    start = command("start", "12.1")
    start.add_argument("title")
    start.add_argument("--details-file")
    for sub in (start, command("next", "12.1")):
        sub.add_argument("--pipeline")
        sub.add_argument("--wait", action="store_true")
    status = command("status", "12.1")
    status.add_argument("job_id", nargs="?")
    status.add_argument("--json", action="store_true")
    for name in ("stop", "resume", "approve", "rework", "delete"):
        sub = command(name, "12.1")
        sub.add_argument("job_id")
        if name == "approve":
            sub.add_argument("--confirm-sensitive", action="store_true")
        elif name == "rework":
            sub.add_argument("note")
        elif name == "delete":
            sub.add_argument("--force", action="store_true")
    command("dashboard", "13.3")
    command("serve", "3.1").add_argument("--port", type=int)
    service = command("service", "3.1").add_subparsers(dest="service_action", required=True)
    service.add_parser("status")
    service.add_parser("stop").add_argument("--stop-running", action="store_true")
    service.add_parser("restart")
    config = command("config", "5.2")
    config.add_argument("--machine", action="store_true", required=True)
    config.add_argument("settings", nargs="*")
    command("version", "12.1")
    return parser


def _repos(args: argparse.Namespace) -> int:
    connection = registry.open_registry()
    try:
        selected = args.repo
        if selected:
            row = connection.execute("SELECT path FROM repos WHERE id=?", (selected, )).fetchone()
            selected = row["path"] if row else selected
        repo = find_repo(selected)
        if repo is not None:
            versioning.check(repo)
            registry.register(connection, repo)
        if args.repos_action == "remove":
            registry.remove(connection, args.id)
        elif args.repos_action == "rename":
            registry.rename(connection, args.id, args.name)
        else:
            print("ID\tNAME\tSTATUS\tPATH")
            for item in registry.list_repos(connection):
                state = "missing" if item.missing else "available"
                print(f"{item.id}\t{item.name}\t{state}\t{item.path}")
        return 0
    finally:
        connection.close()


class RepoNotInitialised(ValueError):
    exit_code = 5


def _selected(args: argparse.Namespace,
              *,
              required: bool = True,
              check_version: bool = True,
              register: bool = True) -> Path | None:
    selected = args.repo
    if selected and not Path(selected).exists() and (machine_dir() / "machine.db").is_file():
        connection = sqlite3.connect((machine_dir() / "machine.db").as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT path FROM repos WHERE id=?", (selected, )).fetchone()
            if row:
                selected = row["path"]
        finally:
            connection.close()
    repo = find_repo(selected)
    if repo is None:
        if required:
            raise RepoNotInitialised("Repo is not initialised; run `factory init` first")
        return None
    if check_version:
        versioning.check(repo)
    if not register:
        return repo
    connection = registry.open_registry()
    try:
        registry.register(connection, repo)
    finally:
        connection.close()
    return repo


def _repo_id(repo: Path) -> str:
    connection = registry.open_registry()
    try:
        return registry.register(connection, repo).id
    finally:
        connection.close()


def _wait(data: dict, base: str, job_id: str) -> int:
    previous = None
    while True:
        result = service.request(data, f"{base}/jobs/{quote(job_id)}")
        job = result["job"]
        state = (job["state"], job["current_step"], job["round"], job["pause_reason"])
        if state != previous:
            print(
                f"{job_id}: {job['state']} · {job['current_step'] or 'queued'} · round {job['round']}"
                + (f" ({job['pause_reason']})" if job["pause_reason"] else ""),
                flush=True)
            previous = state
        if job["state"] in {"paused", "needs_human", "approved", "merged", "cancelled"}:
            return 0
        time.sleep(0.25)


def _run(args: argparse.Namespace) -> int:
    command = args.command
    if command == "init":
        root = scaffold.check_init(Path(args.repo or Path.cwd()).resolve())
        provider = args.provider
        if provider is None:
            provider = input("Client (claude/codex/opencode/both) [codex]: ").strip() or "codex"
            if provider not in {"claude", "codex", "opencode", "both"}:
                raise ValueError("Client must be claude, codex, opencode or both")
        for message in scaffold.init(root, provider, dry_run=args.dry_run):
            print(message)
        if not args.dry_run:
            for check in scaffold.doctor(root):
                print(
                    f"{check['check']}: {'ok' if check['ok'] else 'check'} {check.get('detail', '')}"
                )
            print('Commit the listed files. Check AGENTS.md describes dev-server ports and checks. '
                  'Then run: factory start "your feature"')
        return 0
    if command == "upgrade":
        repo = _selected(args, check_version=False, register=False)
        if repo is None:
            raise RepoNotInitialised("Repo is not initialised; run `factory init` first")
        print(scaffold.upgrade(repo, dry_run=args.dry_run))
        if not args.dry_run:
            running = service.info()
            if service.healthy(running) and running["version"] != __version__:
                service.ensure()
        return 0
    repo = _selected(args,
                     required=command in {
                         "start", "next", "status", "stop", "resume", "approve", "rework", "delete",
                         "dashboard", "eject"
                     })
    if command == "version":
        print(__version__)
        return 0
    if command == "repos":
        return _repos(args)
    if command == "eject":
        print(roles.eject(repo, args.role, args.provider, diff=args.diff), end="\n")
        return 0
    if command == "doctor":
        checks = scaffold.doctor(repo)
        for check in checks:
            print(f"{check['check']}: {'ok' if check['ok'] else 'check'} {check.get('detail', '')}")
        return 0 if all(check["ok"] for check in checks
                        if check["check"] in {"git", "ports"}) else 1
    if command == "serve":
        return service.serve(args.port)
    if command == "service":
        data = service.info()
        if args.service_action == "status":
            print(f"running {service.url(data)} ({data['version']})" if service.
                  healthy(data) else "stopped")
            return 0
        if service.healthy(data):
            service.stop(data, stop_running=getattr(args, "stop_running", False))
        if args.service_action == "restart":
            print(service.url(service.ensure(port=data["port"] if data else None)))
        else:
            print("stopped")
        return 0
    data = service.ensure()
    if command == "config":
        settings = service.request(data, "/api/machine/settings")
        if args.settings:
            for item in args.settings:
                key, separator, value = item.partition("=")
                if not separator or key not in {
                        "concurrency.global", "cli.claude", "cli.codex", "cli.opencode",
                        "ports.range"
                }:
                    raise ValueError(f"Unknown machine setting: {item}")
                group, field = key.split(".")
                settings[group][field] = int(value) if key == "concurrency.global" else (
                    None if value == "null" else value)
            settings = service.request(data, "/api/machine/settings", settings, method="PUT")
        print(json.dumps(settings, indent=2))
        return 0
    repo_id = _repo_id(repo)
    base = f"/api/repos/{quote(repo_id)}"
    dashboard = f"{service.url(data)}/#/repo/{quote(repo_id)}"
    if command == "dashboard":
        webbrowser.open(dashboard)
        print(dashboard)
        return 0
    if command in {"start", "next"}:
        if command == "start":
            details = Path(args.details_file).read_text(
                encoding="utf-8") if args.details_file else ""
            body = {"title": args.title, "details": details, "pipeline": args.pipeline}
            result = service.request(data, base + "/jobs", body)
        else:
            result = service.request(data, base + "/backlog", {
                "action": "start",
                "index": 0,
                "pipeline": args.pipeline
            })
        job_id = result["id"]
        print(f"{job_id}\n{dashboard}/job/{quote(job_id)}", flush=True)
        return _wait(data, base, job_id) if args.wait else 0
    if command == "status":
        path = f"/jobs/{quote(args.job_id)}" if args.job_id else "/state"
        result = service.request(data, base + path)
        role_status = []
        try:
            role_status = service.request(data, base + "/roles")["roles"]
        except (service.ServiceError, OSError) as error:
            print(f"Warning: role provenance unavailable: {error}", file=sys.stderr)
        else:
            result["roles"] = role_status
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for role in role_status:
                if role["outdated"]:
                    print(f"{role['name']} ({role['provider']}): {role['status']}")
            jobs = [result["job"]] if "job" in result else result.get("jobs", [])
            for job in jobs:
                print(f"{job['id']}\t{job['state']}\t{job['current_step'] or ''}\t{job['title']}" +
                      (f"\t{job['pause_reason']}: {job['note'] or ''}"
                       if job["pause_reason"] else ""))
            for job in [result["legacy"]] if "legacy" in result else result.get("legacy_jobs", []):
                print(f"{job['id']}\tLegacy (read-only)\t{job['stage']}\t{job['title']}")
        return 0
    body = {
        "force": getattr(args, "force", False),
        "note": getattr(args, "note", ""),
        "confirm_sensitive": getattr(args, "confirm_sensitive", False)
    }
    result = service.request(data, f"{base}/jobs/{quote(args.job_id)}/{command}", body)
    print(result.get("message") or f"{args.job_id}: {result['job']['state']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (KeyError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return getattr(error, "exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
