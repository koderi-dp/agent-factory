"""Copied out of the dummy tool's venv before running with the base interpreter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


def main() -> None:
    directory = Path(sys.argv[1])
    count = 0
    while not (directory / "stop").exists():
        count += 1
        temporary = directory / "heartbeat.tmp"
        temporary.write_text(json.dumps({
            "pid": os.getpid(),
            "count": count,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "script": str(Path(__file__).resolve()),
        }),
                             encoding="utf-8")
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, directory / "heartbeat.json")
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
