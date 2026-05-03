"""Lightweight on-disk cache for per-paper chat transcripts.

Stored under ``.chat_cache`` (or env ``CHAT_CACHE_DIR``) as
``<paper_id>.json`` with a simple ``{"turns": [...]}`` payload.

Only keeps the last *cap* turns to bound disk usage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

logger = logger.bind(component="ChatHistoryCache")


class ChatHistoryCache:
    _cache_dir: str = os.environ.get("CHAT_CACHE_DIR", ".chat_cache")
    _cap: int = 50  # soft cap – should match ChatState._cap

    # Ensure directory exists at import time
    Path(_cache_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def _safe(cls, raw: str) -> str:
        return "".join(c for c in raw if c.isalnum() or c in ("-", "_")) or "anon"

    @classmethod
    def _path(cls, session_id: str, paper_id: str) -> str:
        safe_session = cls._safe(session_id)
        safe_paper = cls._safe(paper_id)
        return os.path.join(cls._cache_dir, "history", safe_session, f"{safe_paper}.json")

    # -------- public API ---------
    @classmethod
    def save(cls, session_id: str, paper_id: str, turns: list[dict[str, str]]) -> None:
        """Persist *turns* (list of dicts) to disk capped to *cls._cap*."""
        # Ensure directory exists in case it was purged while app is running.
        try:
            Path(cls._cache_dir).mkdir(parents=True, exist_ok=True)
            Path(cls._path(session_id, paper_id)).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # If we cannot create the directory, bail early – persistence is best-effort.
            logger.warning("chat_cache_mkdir_failed", path=cls._cache_dir)
            return

        try:
            turns = turns[-cls._cap :]
            with open(cls._path(session_id, paper_id), "w", encoding="utf-8") as fh:
                json.dump({"turns": turns}, fh)
            logger.debug(
                "chat_cache_saved",
                session_id=session_id,
                paper_id=paper_id,
                turns=len(turns),
            )
        except OSError as exc:
            logger.warning(
                "chat_cache_write_failed",
                session_id=session_id,
                paper_id=paper_id,
                err=str(exc),
            )

    @classmethod
    def load(cls, session_id: str, paper_id: str) -> list[dict[str, str]]:
        """Return list of turns if cached, else empty list."""
        # Ensure directory exists in case it was purged while app is running.
        try:
            Path(cls._cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            # If we cannot create the directory, bail early – persistence is best-effort.
            logger.warning("chat_cache_mkdir_failed", path=cls._cache_dir)
            return []

        try:
            with open(cls._path(session_id, paper_id), encoding="utf-8") as fh:
                data = json.load(fh)
            turns = data.get("turns", [])
            if not isinstance(turns, list):
                raise ValueError("invalid chat cache file")
            logger.debug(
                "chat_cache_loaded",
                session_id=session_id,
                paper_id=paper_id,
                turns=len(turns),
            )
            return turns[-cls._cap :]
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "chat_cache_read_failed",
                session_id=session_id,
                paper_id=paper_id,
                err=str(exc),
            )
            return []
