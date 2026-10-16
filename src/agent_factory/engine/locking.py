"""Process lifetime locks shared by the service and backlog mutations."""

import os
from pathlib import Path


class FileLock:

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.held = False

    def acquire(self) -> bool:
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self.held = True
        return True

    def close(self) -> None:
        if self.held:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            self.close()
            raise ValueError("Another operation holds the file lock; try again")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
