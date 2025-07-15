"""Tiny on-disk cache for LLM-generated summaries (SHA-256 keyed)."""

import atexit
import contextlib
import hashlib
import json
import os
import time
from typing import Optional

from loguru import logger

# Component-level logger
logger = logger.bind(component="SummaryCache")


class SummaryCache:
    """Tiny on-disk cache for LLM-generated summaries (SHA-256 keyed).
    Cached text files are stored in `.summary_cache/` (override via
    `SUMMARY_CACHE_DIR`).
    """

    _cache_dir: str = os.environ.get("SUMMARY_CACHE_DIR", ".summary_cache")
    _cache_json_path: str = os.path.join(_cache_dir, "cache.json")
    _MEM_CACHE: dict[str, str] = {}

    # Default time-to-live (in seconds) for cached summaries.  Can be overridden via
    # the SUMMARY_CACHE_TTL_SEC environment variable.
    TTL_SECONDS: int = int(os.environ.get("SUMMARY_CACHE_TTL_SEC", 24 * 60 * 60))

    # Ensure directory exists at import time
    os.makedirs(_cache_dir, exist_ok=True)

    # Load existing json cache on import
    try:
        if os.path.exists(_cache_json_path):
            with open(_cache_json_path, encoding="utf-8") as _f:
                _MEM_CACHE.update(json.load(_f))
    except (OSError, json.JSONDecodeError) as load_err:
        logger.warning("summary_cache_init_failed", error=str(load_err))

    # Dump on interpreter exit
    @staticmethod
    def _dump(_dummy: None = None) -> None:
        try:
            os.makedirs(SummaryCache._cache_dir, exist_ok=True)
            with open(SummaryCache._cache_json_path, "w", encoding="utf-8") as _f:
                json.dump(SummaryCache._MEM_CACHE, _f)
        except OSError as dump_err:
            logger.warning("summary_cache_dump_failed", error=str(dump_err))

    atexit.register(_dump)

    @classmethod
    def _key(cls, model_id: str, prompt: str, system_prompt: str | None) -> str:
        h = hashlib.sha256()
        h.update(model_id.encode())
        if system_prompt:
            h.update(system_prompt.encode())
        h.update(prompt.encode())
        return h.hexdigest()

    @classmethod
    def get(cls, model_id: str, prompt: str, system_prompt: str | None) -> Optional[str]:
        """Return cached result if present, else None."""
        path = os.path.join(cls._cache_dir, cls._key(model_id, prompt, system_prompt) + ".txt")
        if os.path.exists(path):
            # If the file is older than TTL, treat as a cache miss and delete.
            with contextlib.suppress(Exception):
                if time.time() - os.path.getmtime(path) > cls.TTL_SECONDS:
                    # Best-effort removal; ignore IO errors.
                    with contextlib.suppress(Exception):
                        os.remove(path)
                    logger.debug("cache ttl expired", path=path)
                    return None

            try:
                with open(path, encoding="utf-8") as f:
                    cached = f.read()
                    logger.debug("cache hit", path=path)
                    return cached
            except OSError as read_err:
                logger.warning("failed reading cache file", path=path, error=str(read_err))
                return None
        return None

    @classmethod
    def set(
        cls,
        model_id: str,
        prompt: str,
        system_prompt: str | None,
        content: str,
    ) -> None:
        """Persist *content* for the computed key."""
        path = os.path.join(cls._cache_dir, cls._key(model_id, prompt, system_prompt) + ".txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.debug("cache set", path=path)
        except OSError as write_err:
            # Best-effort caching – ignore IO errors.
            logger.warning("failed writing cache file", path=path, error=str(write_err))

    @classmethod
    def get_generic(cls, key: str) -> Optional[str]:
        """Retrieve cached content by deterministic key."""
        return cls._MEM_CACHE.get(key)

    @classmethod
    def set_generic(cls, key: str, content: str) -> None:
        """Store content under deterministic key and persist to json on exit."""
        cls._MEM_CACHE[key] = content
        logger.debug("generic cache set", key=key)

    # ---------------------------------------------------------------------
    # Maintenance helpers
    # ---------------------------------------------------------------------

    @classmethod
    def clear(cls) -> None:
        """Completely flush both on-disk and in-memory caches."""

        # 1) Clear in-memory map
        cls._MEM_CACHE.clear()
        logger.debug("memory cache cleared")

        # 2) Remove text files + json index
        with contextlib.suppress(Exception):
            for fname in os.listdir(cls._cache_dir):
                # Only delete cache artifacts, not arbitrary files
                if fname.endswith(".txt"):
                    with contextlib.suppress(Exception):
                        os.remove(os.path.join(cls._cache_dir, fname))
            logger.debug("disk cache cleared", dir=cls._cache_dir)

            # Best-effort removal of cache index
            with contextlib.suppress(FileNotFoundError):
                os.remove(cls._cache_json_path)
