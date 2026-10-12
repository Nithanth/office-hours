"""PaperCache unit tests (in-memory temp DB)."""

from __future__ import annotations

from services.paper.cache.paper_cache import PaperCache


def test_upsert_and_get(tmp_path):
    db = tmp_path / "cache.sqlite"
    cache = PaperCache(db_path=str(db), freshness_hours=1)

    meta = {"title": "T"}
    cache.upsert("id1", meta, complete=False)

    rec = cache.get("id1")
    assert rec is not None
    assert rec["meta"]["title"] == "T"
    assert not rec["complete"]

    cache.close()


def test_freshness(tmp_path):
    db = tmp_path / "cache.sqlite"
    cache = PaperCache(
        db_path=str(db), freshness_hours=0
    )  # zero = always stale after insert
    cache.upsert("x", {"a": 1}, complete=True)
    assert not cache.is_fresh("x")
    cache.close()
