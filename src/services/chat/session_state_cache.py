"""Disk-backed session state cache for multi-worker compatibility.

When running multiple uvicorn workers, each worker has its own memory space.
This module ensures session state (paper_cache, stream buffers, etc.) persists
across workers by storing it on disk with file locking for atomicity.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from loguru import logger

logger = logger.bind(component="SessionStateCache")


class SessionStateCache:
    """File-backed session state with POSIX advisory locking (multi-worker safe).

    Each session gets a JSON file: ``<cache_dir>/<session_id>.json``
    """

    _cache_dir: Path = Path(os.environ.get("CHAT_CACHE_DIR", ".chat_cache")) / "sessions"
    _lock_timeout: float = 5.0  # seconds to wait for lock acquisition

    @classmethod
    def _ensure_dir(cls) -> None:
        """Ensure cache directory exists."""
        try:
            cls._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("session_cache_mkdir_failed", path=str(cls._cache_dir), err=str(exc))

    @classmethod
    def _path(cls, session_id: str) -> Path:
        """Return safe file path for session."""
        safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return cls._cache_dir / f"{safe}.json"

    @classmethod
    def _acquire_lock(cls, fd: int, exclusive: bool = True) -> bool:
        """Try to acquire file lock with timeout.

        Args:
            fd: File descriptor
            exclusive: True for write lock, False for read lock

        Returns:
            True if lock acquired, False on timeout
        """
        import errno

        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        start_time = time.time()

        while time.time() - start_time < cls._lock_timeout:
            try:
                fcntl.flock(fd, lock_type | fcntl.LOCK_NB)
                return True
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    raise
                time.sleep(0.01)
        return False

    @classmethod
    def _release_lock(cls, fd: int) -> None:
        """Release file lock."""
        fcntl.flock(fd, fcntl.LOCK_UN)

    @classmethod
    def _lock_path(cls, session_id: str) -> Path:
        """Return a stable lock-file path that survives data-file renames."""
        safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return cls._cache_dir / f"{safe}.lock"

    @classmethod
    def save(cls, session_id: str, state: dict[str, Any]) -> None:
        """Atomically save session state to disk.

        Uses write-to-temp + rename pattern for atomicity.
        Uses a separate .lock file so the advisory lock survives the rename.
        """
        cls._ensure_dir()
        path = cls._path(session_id)
        lock_path = cls._lock_path(session_id)
        temp_path = path.with_suffix(".tmp")

        try:
            # Write to temp file first (atomic rename pattern)
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, default=str)

            # Lock via a *separate* file so the lock inode is stable across renames
            with open(lock_path, "a+") as lock_fh:
                if not cls._acquire_lock(lock_fh.fileno(), exclusive=True):
                    logger.warning("session_cache_lock_timeout", session_id=session_id)
                    return
                try:
                    os.replace(temp_path, path)
                    logger.debug(
                        "session_cache_saved", session_id=session_id, keys=list(state.keys())
                    )
                finally:
                    cls._release_lock(lock_fh.fileno())

        except OSError as exc:
            logger.warning("session_cache_save_failed", session_id=session_id, err=str(exc))
            # Clean up temp file if it exists
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, session_id: str) -> dict[str, Any] | None:
        """Load session state from disk.

        Returns None if no cached state exists.
        Uses shared lock for concurrent reads.
        """
        cls._ensure_dir()
        path = cls._path(session_id)

        if not path.exists():
            return None

        lock_path = cls._lock_path(session_id)

        try:
            # Use the same .lock file as save() for consistent locking
            with open(lock_path, "a+") as lock_fh:
                if not cls._acquire_lock(lock_fh.fileno(), exclusive=False):
                    logger.warning("session_cache_read_lock_timeout", session_id=session_id)
                    return None
                try:
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    logger.debug(
                        "session_cache_loaded", session_id=session_id, keys=list(data.keys())
                    )
                    return cast(dict[str, Any], data)
                finally:
                    cls._release_lock(lock_fh.fileno())

        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            logger.warning("session_cache_corrupted", session_id=session_id, err=str(exc))
            # Try to backup and clear corrupted file
            with suppress(OSError):
                backup_path = path.with_suffix(".corrupted")
                path.rename(backup_path)
                logger.info(
                    "session_cache_backed_up", session_id=session_id, backup=str(backup_path)
                )
            return None
        except OSError as exc:
            logger.warning("session_cache_load_failed", session_id=session_id, err=str(exc))
            return None

    @classmethod
    def delete(cls, session_id: str) -> None:
        """Delete session state from disk."""
        path = cls._path(session_id)
        try:
            path.unlink(missing_ok=True)
            logger.debug("session_cache_deleted", session_id=session_id)
        except OSError as exc:
            logger.warning("session_cache_delete_failed", session_id=session_id, err=str(exc))

    @classmethod
    def list_sessions(cls) -> list[str]:
        """Return list of all cached session IDs."""
        cls._ensure_dir()
        try:
            return [f.stem for f in cls._cache_dir.glob("*.json")]
        except OSError as exc:
            logger.warning("session_cache_list_failed", err=str(exc))
            return []

    @classmethod
    def clear_all(cls) -> int:
        """Clear all session caches. Returns count deleted."""
        count = 0
        try:
            for f in cls._cache_dir.glob("*.json"):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
            logger.info("session_cache_cleared", count=count)
        except OSError as exc:
            logger.warning("session_cache_clear_failed", err=str(exc))
        return count
