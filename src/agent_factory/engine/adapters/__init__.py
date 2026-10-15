"""Provider adapters; unavailable providers never fall back implicitly."""

from typing import Any

from agent_factory.engine.adapters.base import Adapter, UnsupportedProviderError


def get_adapter(provider: str, **kwargs: Any) -> Adapter:
    if provider == "codex":
        from agent_factory.engine.adapters.codex import CodexAdapter
        return CodexAdapter(**kwargs)
    raise UnsupportedProviderError(f"Unsupported provider: {provider}; no adapter is installed")
