"""Opt-in, hard service crashes used by the §16.3 recovery tests."""

import os


def crash_at(point: str) -> None:
    if os.environ.get("FACTORY_CRASH_AT") == point:
        try:
            os.write(2, f"FACTORY_CRASH_AT={point}\n".encode("ascii"))
        finally:
            os._exit(1)
