"""Redis-backed chat store for multi-instance deployments.

Requires the ``redis`` package and a running Redis server.
Configure via ``REDIS_URL`` environment variable (default: redis://localhost:6379/0).

Key schema:
- ``chat:history:{session_id}:{paper_id}`` → JSON list of turns
- ``chat:session:{session_id}`` → JSON dict of session metadata
- ``chat:sessions`` → Redis SET of known session IDs

TTLs are applied to auto-expire stale data.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.chat.stores.base import ChatStore

logger = logger.bind(component="RedisChatStore")

_HISTORY_CAP = 50
_DEFAULT_TTL = 86400 * 7  # 7 days


class RedisChatStore(ChatStore):
    """Redis-backed chat store for horizontal scaling."""

    def __init__(self, redis_url: str = "", ttl: int = _DEFAULT_TTL) -> None:
        try:
            import redis
        except ImportError as exc:
            raise ImportError(
                "Redis backend requires the 'redis' package. "
                "Install with: uv add redis"
            ) from exc

        from config import config

        url = redis_url or getattr(config, "REDIS_URL", None) or "redis://localhost:6379/0"
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._ttl = ttl
        # Verify connection
        try:
            self._client.ping()
            logger.info("redis_connected", url=url)
        except Exception as exc:
            logger.error("redis_connect_failed", url=url, err=str(exc))
            raise

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _history_key(self, session_id: str, paper_id: str) -> str:
        return f"chat:history:{session_id}:{paper_id}"

    def _session_key(self, session_id: str) -> str:
        return f"chat:session:{session_id}"

    def _sessions_set_key(self) -> str:
        return "chat:sessions"

    # ------------------------------------------------------------------
    # Chat history
    # ------------------------------------------------------------------

    def save_history(
        self, session_id: str, paper_id: str, turns: list[dict[str, str]]
    ) -> None:
        key = self._history_key(session_id, paper_id)
        capped = turns[-_HISTORY_CAP:]
        try:
            self._client.setex(key, self._ttl, json.dumps(capped))
            self._client.sadd(self._sessions_set_key(), session_id)
            logger.debug(
                "history_saved", session_id=session_id, paper_id=paper_id, turns=len(capped)
            )
        except Exception as exc:
            logger.warning(
                "history_save_failed", session_id=session_id, paper_id=paper_id, err=str(exc)
            )

    def load_history(
        self, session_id: str, paper_id: str
    ) -> list[dict[str, str]]:
        key = self._history_key(session_id, paper_id)
        try:
            data = self._client.get(key)
            if data is None:
                return []
            turns = json.loads(data)
            return turns[-_HISTORY_CAP:] if isinstance(turns, list) else []
        except Exception as exc:
            logger.warning(
                "history_load_failed", session_id=session_id, paper_id=paper_id, err=str(exc)
            )
            return []

    def delete_history(self, session_id: str, paper_id: str) -> None:
        key = self._history_key(session_id, paper_id)
        try:
            self._client.delete(key)
        except Exception as exc:
            logger.warning("history_delete_failed", err=str(exc))

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------

    def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        key = self._session_key(session_id)
        try:
            self._client.setex(key, self._ttl, json.dumps(state, default=str))
            self._client.sadd(self._sessions_set_key(), session_id)
        except Exception as exc:
            logger.warning("session_save_failed", session_id=session_id, err=str(exc))

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        key = self._session_key(session_id)
        try:
            data = self._client.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as exc:
            logger.warning("session_load_failed", session_id=session_id, err=str(exc))
            return None

    def delete_session(self, session_id: str) -> None:
        try:
            # Delete session metadata
            self._client.delete(self._session_key(session_id))
            # Delete all history keys for this session (scan pattern)
            pattern = f"chat:history:{session_id}:*"
            cursor = 0
            while True:
                cursor, keys = self._client.scan(cursor, match=pattern, count=100)
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break
            # Remove from sessions set
            self._client.srem(self._sessions_set_key(), session_id)
        except Exception as exc:
            logger.warning("session_delete_failed", session_id=session_id, err=str(exc))

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[str]:
        try:
            members = self._client.smembers(self._sessions_set_key())
            return list(members)
        except Exception as exc:
            logger.warning("list_sessions_failed", err=str(exc))
            return []

    def clear_all(self) -> int:
        sessions = self.list_sessions()
        for sid in sessions:
            self.delete_session(sid)
        return len(sessions)
