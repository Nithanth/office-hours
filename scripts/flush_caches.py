#!/usr/bin/env python
"""Utility for clearing Lux Scholar caches.

Usage examples:

    # Clear only LLM summary cache
    python scripts/flush_caches.py --summary

    # Clear only paper metadata cache
    python scripts/flush_caches.py --paper

    # Clear everything (summary + paper)
    python scripts/flush_caches.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

# -----------------------------------------------------------------------------
# Add project 'src' directory to PYTHONPATH so `import services.*` works when
# script is executed directly (`python scripts/flush_caches.py`).
# -----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"

for _pth in (PROJECT_ROOT, SRC_PATH):
    pstr = str(_pth)
    if pstr not in sys.path:
        sys.path.insert(0, pstr)


def _clear_summary_cache() -> None:
    """Clear on-disk and in-memory summary caches."""
    try:
        from services.report.report_utils import (
            SummaryCache,
        )  # local import; avoids hard dep at startup

        SummaryCache.clear()
        logger.info("summary_cache_cleared")
    except Exception as exc:  # noqa: BLE001
        # Emit full traceback for easier debugging
        logger.exception("failed_clearing_summary_cache", error=str(exc))


# Separate helper for SQLite paper cache ------------------------------------------------


def _clear_paper_cache() -> None:
    """Clear the SQLite-backed paper metadata cache."""
    try:
        from services.paper.cache.paper_cache import PaperCache  # local import

        PaperCache().clear()
        logger.info("paper_cache_cleared")
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed_clearing_paper_cache", error=str(exc))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear Lux Scholar caches")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--summary",
        action="store_true",
        help="Flush the LLM summary cache only.",
    )
    group.add_argument(
        "--paper",
        action="store_true",
        help="Flush the paper metadata SQLite cache only.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Flush all supported caches (summary + paper).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.summary or args.all:
        _clear_summary_cache()

    if args.paper or args.all:
        _clear_paper_cache()

    logger.info("cache_flush_complete")


if __name__ == "__main__":
    main()
