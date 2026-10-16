"""Latest activity only; no durable event or unbounded subscriber queues."""

import threading
import time


class Activity:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latest: dict[tuple[str, int], dict] = {}
        self.written: dict[tuple[str, int], float] = {}

    def update(self, repo: str, attempt: int, text: str, at: str) -> bool:
        """Return whether the ten-second persistence interval has elapsed."""
        key = repo, attempt
        now = time.monotonic()
        with self.lock:
            self.latest[key] = {"attempt_id": attempt, "text": text, "at": at}
            persist = now - self.written.get(key, -float("inf")) >= 10
            if persist:
                self.written[key] = now
            return persist

    def current(self, repo: str, running: set[int]) -> list[dict]:
        with self.lock:
            for key in list(self.latest):
                if key[0] == repo and key[1] not in running:
                    self.latest.pop(key)
                    self.written.pop(key, None)
            return [dict(value) for (owner, _), value in self.latest.items() if owner == repo]

    def get(self, repo: str, attempt: int) -> dict | None:
        with self.lock:
            value = self.latest.get((repo, attempt))
            return dict(value) if value else None
