#!/usr/bin/env python
"""Inspect the LanceDB vector index for a single paper.

Usage
-----
$ python scripts/inspect_vector.py --paper-id https://arxiv.org/abs/2006.09661

Optional flags:
  --pdf-url        Also inspect the PDF-keyed index (helpful when chat uses
                   the abs URL but the extractor keyed by the PDF URL, or vice
                   versa).
  --max-rows N     Number of rows to print per table (default: 10).
  --grep PATTERN   Optional case-insensitive regex; only rows whose text matches are shown.

This is purely a debugging helper and has no runtime dependencies outside the
normal Lux Scholar virtualenv.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure `src/` is on PYTHONPATH when script is executed directly (e.g. via
# `python scripts/inspect_vector.py`). This mirrors the approach used by other
# helper scripts in the repo such as `flush_caches.py`.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import lancedb  # noqa: E402
from rich import print  # noqa: E402

# Internal helpers from the codebase (import after path fix)
from services.paper.vector_store import _VECTOR_ROOT, _safe_name  # noqa: E402


def _open_table(db: lancedb.DBConnection, key: str):
    name = _safe_name(key)
    if name not in db.table_names():
        print(f"[red]✗ No index found for key:[/red] {key}")
        return None
    # LanceDB changed `open_table` signature in 0.5 → 0.6; fall back when
    # the `mode` kwarg is not accepted.
    try:
        return db.open_table(name, mode="r")  # >=0.6
    except TypeError:
        return db.open_table(name)  # <0.6


def _inspect_table(table, key: str, max_rows: int):
    # Newer versions: LanceTable.to_list(); older: .to_pandas() or .to_arrow()
    try:
        rows = table.to_list()
    except AttributeError:
        try:
            rows = table.to_pandas().to_dict("records")  # type: ignore[attr-defined]
        except AttributeError:
            # final fallback – get all rows via head(count)
            total = getattr(table, "count_rows", lambda: None)()
            if total is None:
                total = 1000  # arbitrary cap
            rows = table.head(total).to_pylist()  # type: ignore[attr-defined]
    print(f"[green]✓ {len(rows)} rows in index for:[/green] {key}\n")
    for idx, r in enumerate(rows[:max_rows]):
        txt_raw = r.get("text", "")
        txt = textwrap.shorten(txt_raw.replace("\n", " "), width=200)
        print(f"id={idx:>3} | len={len(txt_raw):>4} | {txt}")
    print()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Inspect LanceDB vector index")
    parser.add_argument("--paper-id", required=True, help="Primary key used by chat (abs URL)")
    parser.add_argument(
        "--pdf-url",
        help="Optional second key (PDF URL) to inspect as well",
    )
    parser.add_argument("--max-rows", type=int, default=10, help="Rows to display per table")
    parser.add_argument(
        "--grep",
        help="Optional case-insensitive regex; only rows whose text matches are shown",
    )

    args = parser.parse_args(argv)

    db = lancedb.connect(str(_VECTOR_ROOT))

    import re

    pat = re.compile(args.grep, re.I) if args.grep else None

    for key in filter(None, [args.paper_id, args.pdf_url]):
        tbl = _open_table(db, key)
        if tbl is None:
            continue

        # Need all rows to filter – reuse helper logic but ignore max_rows.
        try:
            rows = tbl.to_list()
        except AttributeError:
            try:
                rows = tbl.to_pandas().to_dict("records")  # type: ignore[attr-defined]
            except AttributeError:
                total = getattr(tbl, "count_rows", lambda: None)()
                if total is None:
                    total = 1000
                rows = tbl.head(total).to_pylist()  # type: ignore[attr-defined]

        if pat:
            rows = [r for r in rows if pat.search(r.get("text", ""))]

        print(f"[green]✓ {len(rows)} rows matched for:[/green] {key}\n")
        for idx, r in enumerate(rows[: args.max_rows]):
            txt_raw = r.get("text", "")
            txt = textwrap.shorten(txt_raw.replace("\n", " "), width=200)
            print(f"id={idx:>3} | len={len(txt_raw):>4} | {txt}")
        print()

    return


if __name__ == "__main__":
    main()
