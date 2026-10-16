"""The agent-factory engine package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-factory-engine")
except PackageNotFoundError:
    __version__ = "0.0.0"
