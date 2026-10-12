# new file
"""Rebuild LanceDB indexes for any cached text whose index is missing or stale.

Usage:
    python scripts/reindex_papers.py [--force] [--dry-run]

Without flags it only (re)builds when the Lance index file is either absent or
older than its source `.txt` cache.
"""

from __future__ import annotations

import argparse
import concurrent.futures as _fut
import os
from pathlib import Path
from typing import Iterable

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from loguru import logger

from services.paper import vector_store as _vs
from services.paper.text_extractor import CACHE_ROOT


def _iter_txt_files() -> Iterable[Path]:
    return CACHE_ROOT.glob("*.txt")


def _derive_id(fp: Path) -> str | None:
    """Return paper_id fragment from *fp.name* (prefix before first underscore).

    Returns None when no prefix – those files were cached without a known paper
    ID and therefore cannot be re-indexed deterministically.
    """

    parts = fp.name.split("_", 1)
    if len(parts) == 2 and parts[0]:
        return parts[0]
    return None


def _needs_rebuild(txt_path: Path, idx_path: Path, *, force: bool) -> bool:
    if force:
        return True
    if not idx_path.exists():
        return True
    return idx_path.stat().st_mtime < txt_path.stat().st_mtime


def _process(txt_path: Path, force: bool, dry_run: bool) -> None:
    paper_id = _derive_id(txt_path)
    if not paper_id:
        logger.debug("skip_no_id", file=str(txt_path))
        return

    idx_path = _vs.index_path(paper_id)
    if not _needs_rebuild(txt_path, idx_path, force=force):
        logger.debug("up_to_date", paper_id=paper_id)
        return

    logger.info("reindex", paper_id=paper_id, txt=str(txt_path))
    if dry_run:
        return

    try:
        text = txt_path.read_text(encoding="utf-8")
        _vs.build_index_from_text(paper_id, text)
    except Exception as exc:  # pragma: no cover
        logger.error("reindex_failed", paper_id=paper_id, err=str(exc))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Refresh missing or stale Lance indexes.")
    ap.add_argument(
        "--force", action="store_true", help="Rebuild even up-to-date indexes"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="List actions without executing"
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=min(4, (os.cpu_count() or 2)),
        help="Parallel workers",
    )
    args = ap.parse_args()

    paths = list(_iter_txt_files())
    if not paths:
        print("No cached texts found under", CACHE_ROOT)
        raise SystemExit(0)

    with _fut.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_process, p, args.force, args.dry_run) for p in paths]
        for f in futures:
            f.result()

    print("Reindexing complete.")
