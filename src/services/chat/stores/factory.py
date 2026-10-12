"""Factory for instantiating the configured chat store backend.

Reads ``CHAT_STORE_BACKEND`` from config (default: "file").
Maintains a module-level singleton so the same store instance is reused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from services.chat.stores.base import ChatStore

_store_instance: ChatStore | None = None


def get_store() -> ChatStore:
    """Return the configured ChatStore singleton.

    Supported backends:
    - ``"file"`` (default): JSON on disk with file locking
    - ``"redis"``: Redis-backed, requires REDIS_URL config
    """
    global _store_instance

    if _store_instance is not None:
        return _store_instance

    from config import config

    backend = getattr(config, "CHAT_STORE_BACKEND", "file").lower()

    if backend == "redis":
        from services.chat.stores.redis_store import RedisChatStore

        redis_url = getattr(config, "REDIS_URL", "") or ""
        _store_instance = RedisChatStore(redis_url=redis_url)
        logger.info("chat_store_initialized", backend="redis")
    else:
        from services.chat.stores.file_store import FileChatStore

        _store_instance = FileChatStore()
        logger.info("chat_store_initialized", backend="file")

    return _store_instance


def reset_store() -> None:
    """Reset the singleton (useful for testing)."""
    global _store_instance
    _store_instance = None
