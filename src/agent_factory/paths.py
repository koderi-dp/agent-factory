"""Machine storage and initialised repository discovery."""

import os
from pathlib import Path

from platformdirs import user_data_path


def machine_dir() -> Path:
    """Resolve on each call so callers can isolate state with FACTORY_HOME."""
    override = os.environ.get("FACTORY_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return user_data_path("agent-factory", appauthor=False).resolve()


def normalize_repo_path(path: str | Path) -> Path:
    """Collapse aliases, including case on Windows, before hashing or storing."""
    return Path(os.path.normcase(str(Path(path).expanduser().resolve())))


def find_repo(start: str | Path | None = None) -> Path | None:
    """Find the nearest ancestor holding factory/config.json."""
    path = Path(start if start is not None else Path.cwd()).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "factory" / "config.json").is_file():
            return normalize_repo_path(candidate)
    return None
