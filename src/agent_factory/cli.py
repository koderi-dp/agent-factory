"""Command skeleton; engine and service commands are filled in by later sessions."""

import argparse
import sys

from agent_factory import __version__
from agent_factory.engine import registry
from agent_factory.paths import find_repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="Repository path or registered id", default=argparse.SUPPRESS)
    parser.add_argument("--repo", help="Repository path or registered id")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, section: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common])
        sub.set_defaults(section=section)
        return sub

    init = command("init", "12.2")
    init.add_argument("--provider", choices=["claude", "codex", "both"])
    init.add_argument("--dry-run", action="store_true")
    command("upgrade", "15.4").add_argument("--dry-run", action="store_true")
    eject = command("eject", "5.3")
    eject.add_argument("role")
    eject.add_argument("--provider", choices=["claude", "codex"])
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
        # TODO (plan §12.1, session 1.9): shared repo selection, version gate,
        # auto-registration, and HTTP dispatch for service-backed commands.
        selected = args.repo
        if selected:
            row = connection.execute("SELECT path FROM repos WHERE id=?", (selected,)).fetchone()
            selected = row["path"] if row else selected
        repo = find_repo(selected)
        if repo is not None:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "repos":
        try:
            return _repos(args)
        except (KeyError, ValueError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 1
    # TODO: implement each command in its referenced plan section (sessions 1.2+).
    print(f"not implemented yet (plan §{args.section})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
