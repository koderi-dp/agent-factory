#!/usr/bin/env python3
"""Optional bootstrap for installing agent-factory with uv."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"
UV_WINDOWS_INSTALL = "https://astral.sh/uv/install.ps1"
UV_UNIX_INSTALL = "https://astral.sh/uv/install.sh"


def install_uv() -> bool:
    """Offer uv's official installer when uv is not available on PATH."""
    if shutil.which("uv"):
        return True
    print(f"uv is required. Install instructions: {UV_INSTALL_URL}")
    answer = input("Run uv's official installer now? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        return False
    if sys.platform == "win32":
        command = [
            "powershell", "-ExecutionPolicy", "ByPass", "-c", f"irm {UV_WINDOWS_INSTALL} | iex"
        ]
    else:
        command = ["sh", "-c", f"curl -LsSf {UV_UNIX_INSTALL} | sh"]
    result = subprocess.run(command, check=False)
    if result.returncode:
        print("uv installation failed.", file=sys.stderr)
        return False
    return bool(shutil.which("uv"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target",
                        type=Path,
                        help="Initialize this existing repository after installing")
    args = parser.parse_args(argv)
    target = args.target.expanduser().resolve() if args.target is not None else None
    if target is not None and not target.is_dir():
        print(f"Target must be an existing folder: {target}", file=sys.stderr)
        return 1
    if not install_uv():
        return 1
    result = subprocess.run(["uv", "tool", "install", "agent-factory-engine"], check=False)
    if result.returncode:
        return result.returncode
    if target is not None:
        return subprocess.run(["factory", "init"], cwd=target, check=False).returncode
    print("Installed. Run `factory init` in your repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
