"""File-based chat store — JSON files on disk with advisory locking.

This is the default backend that preserves the original behaviour of
``ChatHistoryCache`` and ``SessionStateCache`` in a unified interface.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from services.chat.stores.base import ChatStore

logger = logger.bind(component="FileChatStore")

_HISTORY_CAP = 50  # max turns kept per paper
_LOCK_TIMEOUT = 5.0  # seconds


def _safe_name(raw: str) -> str:
    """Sanitise a string for use as a filename component."""
    return "".join(c for c in raw if c.isalnum() or c in ("-", "_")) or "anon"


class FileChatStore(ChatStore):
    """JSON-on-disk chat store with POSIX file locking."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir or os.environ.get("CHAT_CACHE_DIR", ".chat_cache"))
        self._history_dir = self._base / "history"
        self._sessions_dir = self._base / "sessions"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (self._history_dir, self._sessions_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("file_store_mkdir_failed", path=str(d), err=str(exc))

    # ------------------------------------------------------------------
    # Locking helpers
    # ------------------------------------------------------------------

    def _acquire_lock(self, fd: int, exclusive: bool = True) -> bool:
        import errno

        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        start = time.time()
        while time.time() - start < _LOCK_TIMEOUT:
            try:
                fcntl.flock(fd, lock_type | fcntl.LOCK_NB)
                return True
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    raise
                time.sleep(0.01)
        return False

    def _release_lock(self, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Chat history
    # ------------------------------------------------------------------

    def _history_path(self, session_id: str, paper_id: str) -> Path:
        session_dir = self._history_dir / _safe_name(session_id)
        return session_dir / f"{_safe_name(paper_id)}.json"

    def save_history(
        self, session_id: str, paper_id: str, turns: list[dict[str, str]]
    ) -> None:
        path = self._history_path(session_id, paper_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            capped = turns[-_HISTORY_CAP:]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"turns": capped}, fh)
            logger.debug(
                "history_saved",
                session_id=session_id,
                paper_id=paper_id,
                turns=len(capped),
            )
        except OSError as exc:
            logger.warning(
                "history_save_failed",
                session_id=session_id,
                paper_id=paper_id,
                err=str(exc),
            )

    def load_history(
        self, session_id: str, paper_id: str
    ) -> list[dict[str, str]]:
        path = self._history_path(session_id, paper_id)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            turns = data.get("turns", [])
            if not isinstance(turns, list):
                return []
            return turns[-_HISTORY_CAP:]
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "history_load_failed",
                session_id=session_id,
                paper_id=paper_id,
                err=str(exc),
            )
            return []

    def delete_history(self, session_id: str, paper_id: str) -> None:
        path = self._history_path(session_id, paper_id)
        with suppress(OSError):
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{_safe_name(session_id)}.json"

    def _session_lock_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{_safe_name(session_id)}.lock"

    def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._session_path(session_id)
        lock_path = self._session_lock_path(session_id)
        temp_path = path.with_suffix(".tmp")

        try:
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, default=str)

            with open(lock_path, "a+") as lock_fh:
                if not self._acquire_lock(lock_fh.fileno(), exclusive=True):
                    logger.warning("session_lock_timeout", session_id=session_id)
                    return
                try:
                    os.replace(temp_path, path)
                finally:
                    self._release_lock(lock_fh.fileno())

        except OSError as exc:
            logger.warning("session_save_failed", session_id=session_id, err=str(exc))
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None

        lock_path = self._session_lock_path(session_id)
        try:
            with open(lock_path, "a+") as lock_fh:
                if not self._acquire_lock(lock_fh.fileno(), exclusive=False):
                    return None
                try:
                    with open(path, encoding="utf-8") as fh:
                        return json.load(fh)
                finally:
                    self._release_lock(lock_fh.fileno())
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("session_load_failed", session_id=session_id, err=str(exc))
            return None

    def delete_session(self, session_id: str) -> None:
        # Delete session metadata
        with suppress(OSError):
            self._session_path(session_id).unlink(missing_ok=True)
        with suppress(OSError):
            self._session_lock_path(session_id).unlink(missing_ok=True)
        # Delete all history for this session
        session_dir = self._history_dir / _safe_name(session_id)
        if session_dir.exists():
            for f in session_dir.glob("*.json"):
                with suppress(OSError):
                    f.unlink()
            with suppress(OSError):
                session_dir.rmdir()

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[str]:
        try:
            return [f.stem for f in self._sessions_dir.glob("*.json")]
        except OSError:
            return []

    def clear_all(self) -> int:
        sessions = self.list_sessions()
        for sid in sessions:
            self.delete_session(sid)
        return len(sessions)
