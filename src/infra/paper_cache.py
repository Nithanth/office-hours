import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, cast


class PaperCache:
    """Lightweight on-disk cache for paper metadata.

    Uses the std-lib `sqlite3` module so no external services are required.
    A single table keeps the latest known metadata for each Exa document-id.
    """

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS paper_cache (
            id           TEXT PRIMARY KEY,
            metadata     TEXT NOT NULL,
            last_checked TEXT NOT NULL,
            complete     INTEGER NOT NULL
        );
        """

    def __init__(self, db_path: str | None = None, freshness_hours: int = 24):
        # Normalise and resolve DB path early so subsequent ops use a concrete str
        env_path = os.getenv("PAPER_CACHE_DB")
        self.db_path: str = str(db_path or env_path or "data/paper_cache.sqlite3")

        # Ensure parent directory exists
        Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute(self._CREATE_SQL)
        self.conn.commit()
        self.freshness_td = timedelta(hours=freshness_hours)

    # -------- Public helpers -------------------------------------------------

    def get(self, doc_id: str) -> Optional[dict[str, Any]]:
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
        last_checked = datetime.fromisoformat(last_checked_str).replace(tzinfo=timezone.utc)
        return {
            "meta": meta,
            "last_checked": last_checked,
            "complete": bool(complete_int),
        }

    def is_fresh(self, doc_id: str) -> bool:
        rec = self.get(doc_id)
        if not rec:
            return False
        last_checked = cast(datetime, rec["last_checked"])
        return bool(datetime.now(timezone.utc) - last_checked < self.freshness_td)

    def upsert(self, doc_id: str, meta: dict[str, Any], complete: bool) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(meta, default=str)  # fallback str serialisation for datetimes
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

    def get_incomplete(self, limit: int = 100) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM paper_cache WHERE complete = 0 ORDER BY last_checked LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Cache invalidation helpers
    # ------------------------------------------------------------------

    def delete(self, doc_id: str) -> None:
        """Remove a single record from the cache (silently ignore if *doc_id* unknown)."""
        self.conn.execute("DELETE FROM paper_cache WHERE id = ?", (doc_id,))
        self.conn.commit()

    def clear(self) -> None:
        """Purge **all** cached entries. Use with care - this is irreversible."""
        self.conn.execute("DELETE FROM paper_cache;")
        self.conn.commit()
