"""On-disk SQLite cache for paper metadata."""

# stdlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Optional, cast

# external
from loguru import logger

import config as _cfg


class PaperCache:
    """SQLite-backed metadata cache.

    Provides simple *get*/ *upsert* helpers, freshness checks, and can be used
    as a context-manager::

        with PaperCache() as cache:
            cache.upsert("123", {...}, True)

    WAL journal mode is enabled for safe concurrent reads; an index on
    ``(complete, last_checked)`` accelerates the *get_incomplete* query.
    """

    # ------------------------------------------------------------------
    # Table / index DDL
    # ------------------------------------------------------------------

    # table schema
    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS paper_cache (
            id           TEXT PRIMARY KEY,
            metadata     TEXT NOT NULL,
            last_checked TEXT NOT NULL,
            complete     INTEGER NOT NULL
        );
        """

    _CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_comp_last ON paper_cache (complete, last_checked);"

    def __init__(self, db_path: str | None = None, freshness_hours: int = 24):
        # Resolve DB path early so subsequent ops use a concrete str
        env_path = _cfg.config.PAPER_CACHE_DB
        self.db_path: str = str(db_path or env_path or "data/paper_cache.sqlite3")

        # Ensure parent directory exists
        Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute(self._CREATE_SQL)
        self.conn.execute(self._CREATE_INDEX_SQL)
        self.conn.commit()
        self.freshness_td = timedelta(hours=freshness_hours)
        logger.debug("paper_cache_init", path=self.db_path)

    # Public helpers
    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
        """Return cached metadata record or None if *doc_id* missing."""
        row = self.conn.execute(
            "SELECT metadata, last_checked, complete FROM paper_cache WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        meta_json, last_checked_str, complete_int = row
        try:
            meta = json.loads(meta_json)
        except Exception:
            meta = {}
        last_checked = datetime.fromisoformat(last_checked_str).replace(tzinfo=UTC)
        rec = {
            "meta": meta,
            "last_checked": last_checked,
            "complete": bool(complete_int),
        }
        logger.debug("paper_cache_get", id=doc_id, found=bool(rec))
        return rec

    def is_fresh(self, doc_id: str) -> bool:
        """True if cache entry exists and is newer than *freshness_td*."""
        rec = self.get(doc_id)
        if not rec:
            return False
        last_checked = cast(datetime, rec["last_checked"])
        fresh = bool(datetime.now(UTC) - last_checked < self.freshness_td)
        logger.debug("paper_cache_is_fresh", id=doc_id, fresh=fresh)
        return fresh

    def upsert(self, doc_id: str, meta: dict[str, Any], complete: bool) -> None:
        """Insert or update *doc_id* row with *meta* and *complete* flag."""
        now_str = datetime.now(UTC).isoformat()
        meta_json = json.dumps(
            meta, default=str
        )  # fallback str serialisation for datetimes
        self.conn.execute(
            """
            INSERT INTO paper_cache(id, metadata, last_checked, complete)
            VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                metadata = excluded.metadata,
                last_checked = excluded.last_checked,
                complete = excluded.complete;
            """,
            (doc_id, meta_json, now_str, int(complete)),
        )
        self.conn.commit()
        logger.debug("paper_cache_upsert", id=doc_id, complete=complete)

    def get_incomplete(self, limit: int = 100) -> list[str]:
        """Return up to *limit* doc_ids whose metadata is incomplete."""
        rows = self.conn.execute(
            "SELECT id FROM paper_cache WHERE complete = 0 ORDER BY last_checked LIMIT ?",
            (limit,),
        ).fetchall()
        ids = [r[0] for r in rows]
        logger.debug("paper_cache_incomplete", count=len(ids))
        return ids

    # Cache invalidation helpers
    def delete(self, doc_id: str) -> None:
        """Delete *doc_id* from cache; no-op if not present."""
        self.conn.execute("DELETE FROM paper_cache WHERE id = ?", (doc_id,))
        self.conn.commit()
        logger.debug("paper_cache_delete", id=doc_id)

    def clear(self) -> None:
        """Remove every cache entry (irreversible)."""
        self.conn.execute("DELETE FROM paper_cache;")
        self.conn.commit()
        logger.warning("paper_cache_cleared")

    # ------------------------------------------------------------------
    # Lifecycle helpers --------------------------------------------------
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        try:
            self.conn.close()
        except Exception as exc:  # pragma: no cover – best-effort close
            logger.debug("paper_cache_close_failed", err=exc)

    # Context-manager sugar ------------------------------------------------

    def __enter__(self) -> "PaperCache":  # noqa: D401 – returns self
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:  # noqa: D401 – context exit
        """Context-manager exit – always re-raises any exception."""

        self.close()
        # Propagate any exception (do not suppress)
        return False
