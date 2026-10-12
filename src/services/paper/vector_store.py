"""Unified vector store built on *lancedb*.

All papers share a single LanceDB table called ``"papers"`` with a ``paper_id``
column on every row.  This enables cross-paper search while keeping per-paper
operations (index, delete, search) fast via filtered queries.

Legacy per-paper tables (created by earlier versions) are migrated into the
shared table on first access (best-effort).
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import lancedb
import numpy as np
import tiktoken
from lancedb.table import Table
from loguru import logger

from config import config
from services.paper.embedding import embed_texts

# Cross-encoder is optional – lazy import so test envs pass without it.
CrossEncoder: Any
try:
    from sentence_transformers import CrossEncoder as _CrossEncoder

    CrossEncoder = _CrossEncoder
except ImportError:  # pragma: no cover – optional dep
    CrossEncoder = None

_VECTOR_ROOT: Path = cast(Path, config.VECTOR_CACHE_DIR)
_VECTOR_ROOT.mkdir(parents=True, exist_ok=True)

_TOKENIZER = tiktoken.get_encoding("cl100k_base")  # good default for GPT models
_MAX_TOKENS = config.RAG_CHUNK_TOKENS
_OVERLAP = config.RAG_CHUNK_OVERLAP

# Regex for detecting citation lines – leading number then year.
_CITE_LINE_RE = re.compile(r"^\s*(?:\[?\d+\]?|\d+\.)\s+.*?(?:19|20)\d{2}")

# Retrieval filtering thresholds (configurable via config.py)
_MIN_CHARS = config.RAG_MIN_CHARS  # ignore short chunks
# Max L2 distance to keep (strict phase)
_DISTANCE_THRESH = config.RAG_DISTANCE_L2
# Number of database rows to inspect per *desired* result.
# Larger values increase recall at the cost of latency.
# Tunable via env var `RAG_SEARCH_MULTIPLIER`, default 25 → 100 candidates for
# the common `top_k=4`.
_SEARCH_MULTIPLIER = config.RAG_SEARCH_MULTIPLIER

# Name of the single shared table
_TABLE_NAME = "papers"

# ---------------------------------------------------------------------------
# Optional cross-encoder rerank (query ⊕ chunk for fine-grained scoring)
# ---------------------------------------------------------------------------

_CE_MODEL: CrossEncoder | None = None  # quoted for typing when dep absent
_CE_POOL = config.CE_RERANK_POOL  # how many candidates to rerank
_CE_PAIR_CACHE: dict[
    tuple[str, str], float
] = {}  # Simple in-process cache for (query, chunk_text) → ce_score


def _load_ce() -> CrossEncoder:
    """Lazy-load the cross-encoder singleton, raising if unavailable."""

    if CrossEncoder is None:  # dependency not installed
        raise RuntimeError(
            "CrossEncoder requested but 'sentence-transformers' is not installed. "
            "Install it or disable ENABLE_CE_RERANK."
        )

    global _CE_MODEL
    if _CE_MODEL is None:
        _CE_MODEL = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _CE_MODEL


def _rerank(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *hits* sorted by cross-encoder relevance score (desc)."""

    model = _load_ce()

    # Retrieve cached scores or mark for batch prediction
    missing_pairs: list[tuple[str, str]] = []
    missing_indices: list[int] = []
    scores: list[float] = []

    for idx, h in enumerate(hits):
        key = (query, h["text"])
        if key in _CE_PAIR_CACHE:
            scores.append(_CE_PAIR_CACHE[key])
        else:
            scores.append(0.0)  # placeholder
            missing_pairs.append(key)
            missing_indices.append(idx)

    if missing_pairs:
        new_scores = model.predict(missing_pairs, convert_to_numpy=True)
        for idx, score in zip(missing_indices, new_scores, strict=False):
            scores[idx] = float(score)
            _CE_PAIR_CACHE[missing_pairs[idx]] = float(score)

    for h, s in zip(hits, scores, strict=False):
        h["_ce_score"] = s
    return sorted(hits, key=lambda r: r["_ce_score"], reverse=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_name(paper_id: str) -> str:
    """Return deterministic filename component from *paper_id* (no slashes)."""

    h = hashlib.sha1(paper_id.encode("utf-8")).hexdigest()[:16]
    return h


def _get_db() -> lancedb.DBConnection:
    """Return a connection to the shared LanceDB database."""
    return lancedb.connect(str(_VECTOR_ROOT))


def _table_names(db: lancedb.DBConnection) -> list[str]:
    """Return table names, handling API deprecation (table_names -> list_tables)."""
    try:
        result = db.table_names()
        return list(result) if result else []
    except (AttributeError, TypeError):
        pass
    # Fallback: newer API returns a response object with a .tables attribute
    try:
        resp = db.list_tables()
        if hasattr(resp, "tables"):
            return list(resp.tables)
        return list(resp)
    except Exception:
        return []


def _open_shared_table(db: lancedb.DBConnection) -> Table | None:
    """Open the shared papers table, returning None if it doesn't exist."""
    if _TABLE_NAME not in _table_names(db):
        return None
    try:
        return cast(Table, db.open_table(_TABLE_NAME, mode="r"))
    except TypeError:
        return cast(Table, db.open_table(_TABLE_NAME))


def _open_shared_table_rw(db: lancedb.DBConnection) -> Table | None:
    """Open the shared papers table in read-write mode, returning None if it doesn't exist."""
    if _TABLE_NAME not in _table_names(db):
        return None
    try:
        return cast(Table, db.open_table(_TABLE_NAME))
    except TypeError:
        return cast(Table, db.open_table(_TABLE_NAME))


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

_MIGRATION_DONE: bool = False


def _migrate_legacy_tables() -> None:
    """Best-effort migration of legacy per-paper tables into the shared table.

    Legacy tables are identified as any table name that is NOT the shared table
    name.  Each legacy table's rows are augmented with a ``paper_id`` column
    (set to the table name / hash) and appended to the shared table.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    _MIGRATION_DONE = True

    db = _get_db()
    all_tables = _table_names(db)

    # Also check for legacy .lance directories on disk that lancedb might
    # not list (older versions created directories with .lance suffix).
    legacy_names: list[str] = []
    for name in all_tables:
        if name != _TABLE_NAME:
            legacy_names.append(name)

    # Check for .lance directories on disk that might not be in table_names
    for p in _VECTOR_ROOT.iterdir():
        if p.is_dir() and p.name.endswith(".lance"):
            stem = p.name[: -len(".lance")]
            if stem != _TABLE_NAME and stem not in legacy_names:
                legacy_names.append(stem)

    if not legacy_names:
        return

    logger.info(
        "vector_store_migration_start",
        legacy_tables=len(legacy_names),
    )

    shared_table = _open_shared_table_rw(db)

    for legacy_name in legacy_names:
        try:
            # Try to open the legacy table
            try:
                ltable = db.open_table(legacy_name, mode="r")
            except TypeError:
                ltable = db.open_table(legacy_name)
            except Exception:
                # Table might not be openable (e.g. .lance dir not registered)
                continue

            # Read all rows
            try:
                rows = ltable.to_arrow().to_pylist()
            except Exception:
                try:
                    rows = ltable.to_pandas().to_dict("records")
                except Exception:
                    logger.warning(
                        "vector_store_migration_skip",
                        table=legacy_name,
                        reason="cannot read rows",
                    )
                    continue

            if not rows:
                continue

            # Add paper_id column – use the legacy hash as paper_id
            for row in rows:
                row["paper_id"] = legacy_name

            # Insert into shared table
            if shared_table is None:
                db.create_table(_TABLE_NAME, data=rows, mode="overwrite")
                shared_table = _open_shared_table_rw(db)
            else:
                shared_table.add(rows)

            # Drop the legacy table
            with suppress(Exception):
                db.drop_table(legacy_name)

            logger.info(
                "vector_store_migrated_table",
                legacy_name=legacy_name,
                rows=len(rows),
            )
        except Exception as exc:
            logger.warning(
                "vector_store_migration_failed",
                table=legacy_name,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def index_exists(paper_id: str) -> bool:
    """Check if rows exist for *paper_id* in the shared table."""
    _migrate_legacy_tables()

    db = _get_db()
    table = _open_shared_table(db)
    if table is None:
        return False

    # Use pyarrow filtering (fast, no vector needed).
    try:
        import pyarrow.compute as pc

        arrow_tbl = table.to_arrow()
        mask = pc.call_function("equal", [arrow_tbl.column("paper_id"), paper_id])
        filtered = arrow_tbl.filter(mask)
        return len(filtered) > 0
    except Exception:
        # Last resort: scan with SQL-like filter
        try:
            results = (
                table.search().where(f"paper_id = '{paper_id}'").limit(1).to_list()
            )
            return len(results) > 0
        except Exception:
            return False


def save_index(
    paper_id: str,
    embeddings: np.ndarray,
    chunks: list[str],
    *,
    headings: list[str] | None = None,
) -> None:
    """Write (or overwrite) rows for *paper_id* in the shared table."""

    if embeddings.shape[0] != len(chunks):
        raise ValueError("embeddings/chunks length mismatch")

    _migrate_legacy_tables()

    db = _get_db()

    # Build row data
    data: list[dict[str, Any]] = []
    for idx, (vec, txt) in enumerate(zip(embeddings, chunks, strict=False)):
        stripped = txt.lstrip()
        heading_val = headings[idx] if headings else ""
        meta = {
            "paper_id": paper_id,
            "vector": vec.tolist(),
            "text": txt,
            "is_table": stripped.startswith("=== TABLE"),
            "is_figure": stripped.startswith("=== FIGURE"),
            "section": heading_val,
        }
        data.append(meta)

    # Check if shared table exists
    table = _open_shared_table_rw(db)
    if table is None:
        # Create the shared table with the first batch of data
        db.create_table(_TABLE_NAME, data=data, mode="overwrite")
    else:
        # Delete existing rows for this paper_id, then add new ones
        try:
            table.delete(f"paper_id = '{paper_id}'")
        except Exception as exc:
            logger.warning(
                "vector_store_delete_failed",
                paper_id=paper_id,
                error=str(exc),
            )
        table.add(data)

    logger.info("vector_index_built", paper_id=paper_id, chunks=len(chunks))


def _apply_filtering(
    candidates: list[dict[str, Any]],
    query_text: str,
    top_k: int,
    t_search_ms: float,
) -> list[dict[str, Any]]:
    """Apply two-phase filtering logic to candidate results.

    Shared by both search() and search_all().
    """

    def _is_special(txt: str) -> bool:
        """Return True for table or figure sentinel chunks."""
        stripped = txt.lstrip()
        return stripped.startswith("=== TABLE") or stripped.startswith("=== FIGURE")

    # First pass – apply both min-char and distance constraints.
    strict = [
        r
        for r in candidates
        if (len(r["text"]) >= _MIN_CHARS or _is_special(r["text"]))
        and r.get("_distance", 9e9) <= _DISTANCE_THRESH
    ]
    if strict:
        logger.debug(
            "rag_phase", phase="strict", kept=len(strict), search_ms=t_search_ms
        )
        return cast(list[dict[str, Any]], strict[:top_k])

    # Second pass – relax distance threshold, keep only length filter so we
    # still surface something relevant (best-effort).
    relaxed = [
        r for r in candidates if len(r["text"]) >= _MIN_CHARS or _is_special(r["text"])
    ]
    if relaxed:
        # ---------------- keyword-boost re-rank ----------------
        qt_lower = query_text.lower()
        want_table = "table" in qt_lower
        want_figure = "figure" in qt_lower or "fig." in qt_lower

        q_words = {
            w
            for w in re.findall(r"[A-Za-z]{3,}", qt_lower)
            if w not in {"the", "and", "with", "that", "from"}
        }

        def _kw_hits(row: dict[str, Any]) -> int:
            tl = row["text"].lower()
            return sum(1 for w in q_words if w in tl)

        def _sort_key(row: dict[str, Any]) -> tuple[int, int, float]:
            pri = 0
            if (
                want_table
                and row["text"].lstrip().startswith("=== TABLE")
                or want_figure
                and row["text"].lstrip().startswith("=== FIGURE")
            ):
                pri = -1
            return (pri, -_kw_hits(row), row.get("_distance", 9e9))

        relaxed.sort(key=_sort_key)
        logger.debug(
            "rag_phase",
            phase="relaxed_kw",
            kept=len(relaxed[:top_k]),
            search_ms=t_search_ms,
        )
        return cast(list[dict[str, Any]], relaxed[:top_k])

    # Last resort – return the highest-ranked chunks regardless of distance so the
    # caller can still display *something* (better than an empty chat response).
    if candidates:
        logger.debug(
            "rag_phase",
            phase="fallback",
            kept=len(candidates[:top_k]),
            search_ms=t_search_ms,
        )
        return cast(list[dict[str, Any]], candidates[:top_k])

    return []


def _do_search(
    query_embedding: np.ndarray,
    *,
    query_text: str = "",
    top_k: int = 12,
    where_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Core search logic shared by search() and search_all().

    Performs vector search on the shared table, optionally filtered by
    *where_filter*, applies two-phase filtering, and optional cross-encoder
    reranking.
    """
    _migrate_legacy_tables()

    db = _get_db()
    table = _open_shared_table(db)
    if table is None:
        return []

    # Start elapsed-time measurement for search + optional rerank
    t0 = time.perf_counter()

    # Fetch a wider pool to avoid missing relevant sections that are slightly
    # further down the similarity ranking.
    limit = top_k * _SEARCH_MULTIPLIER

    try:
        query_builder = table.search(query_embedding.tolist())
        if where_filter:
            query_builder = query_builder.where(where_filter)
        candidates = query_builder.limit(limit).to_list()
    except AttributeError:
        try:
            query_builder = table.search(query_embedding.tolist())
            if where_filter:
                query_builder = query_builder.where(where_filter)
            candidates = query_builder.limit(limit).to_pandas().to_dict("records")
        except AttributeError:
            query_builder = table.search(query_embedding.tolist())
            if where_filter:
                query_builder = query_builder.where(where_filter)
            arrow_tbl = query_builder.limit(limit).to_arrow()
            candidates = arrow_tbl.to_pylist()

    t_search_ms = round((time.perf_counter() - t0) * 1000, 1)

    # -------- optional cross-encoder rerank --------
    if config.ENABLE_CE_RERANK and query_text:
        pool = candidates[:_CE_POOL]
        candidates = _rerank(query_text, pool) + candidates[_CE_POOL:]
        t_total_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.debug(
            "rag_phase",
            phase="ce_rerank",
            kept=len(pool),
            search_ms=t_search_ms,
            total_ms=t_total_ms,
        )

    return _apply_filtering(candidates, query_text, top_k, t_search_ms)


def search(
    paper_id: str,
    query_embedding: np.ndarray,
    *,
    query_text: str = "",
    top_k: int = 12,
) -> list[dict]:
    """Return *top_k* records most relevant to *query_text* for a single paper.

    Two-phase filtering:
    1. Strict → distance + length thresholds.
    2. Relaxed → apply only length, then boost rows whose content shares
       keywords with *query_text* (helps surface `=== TABLE` chunks when the
       user explicitly mentions a metric or "table").
    """
    where_filter = f"paper_id = '{paper_id}'"
    return _do_search(
        query_embedding,
        query_text=query_text,
        top_k=top_k,
        where_filter=where_filter,
    )


def search_all(
    query_embedding: np.ndarray,
    *,
    query_text: str = "",
    top_k: int = 12,
    paper_ids: list[str] | None = None,
) -> list[dict]:
    """Search across ALL papers (or a subset if *paper_ids* is provided).

    Returns results with ``paper_id`` included in each dict.  Uses the same
    two-phase filtering and optional cross-encoder reranking as ``search()``.
    """
    where_filter: str | None = None
    if paper_ids is not None and len(paper_ids) > 0:
        # Build an IN filter for the subset
        escaped = [pid.replace("'", "''") for pid in paper_ids]
        in_list = ", ".join(f"'{pid}'" for pid in escaped)
        where_filter = f"paper_id IN ({in_list})"

    return _do_search(
        query_embedding,
        query_text=query_text,
        top_k=top_k,
        where_filter=where_filter,
    )


def build_index_from_text(
    paper_id: str,
    text: str,
    *,
    figure_descriptions: list[tuple[str, int]] | None = None,
) -> None:
    """Chunk *text*, embed each chunk, and persist the LanceDB index.

    Parameters
    ----------
    paper_id
        Unique paper identifier.
    text
        Full extracted text of the paper.
    figure_descriptions
        Optional list of (description_text, page_number) tuples from the
        figure extractor.  Each description is added as an additional chunk
        with ``is_figure=True`` so it is retrievable when users ask about
        figures/diagrams.
    """

    chunk_tuples = list(_chunk_text(text))  # (chunk_text, heading)
    if not chunk_tuples:
        return

    chunk_texts = [c for c, _ in chunk_tuples]
    headings = [h for _, h in chunk_tuples]

    # Append figure description chunks so they are searchable via RAG.
    if figure_descriptions:
        for desc, page_num in figure_descriptions:
            if desc.strip():
                chunk_texts.append(f"=== FIGURE (page {page_num}) ===\n{desc}")
                headings.append(f"Figure (page {page_num})")

    # Batch size can be tuned via EMB_BATCH_SIZE env var inside embed_texts()
    embeddings = embed_texts(chunk_texts)

    # Optional: halve storage size by persisting float16 instead of float32.
    import config as _cfg

    if _cfg.config.EMB_FLOAT16:
        embeddings = embeddings.astype("float16")

    save_index(paper_id, embeddings, chunk_texts, headings=headings)


# ---------------------------------------------------------------------------
# Internal – text chunking
# ---------------------------------------------------------------------------

# Heading lines like "2. Related Work" or "4.2 Ablations" (numbered) followed by title-case word(s)
_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\s+[A-Z][A-Za-z-]+")


def _chunk_text(text: str) -> Iterable[tuple[str, str]]:
    """Yield overlapping token windows, starting new windows on section headings.

    This preserves headings at the *start* of a chunk so keyword and sentinel boosts
    work better.  Implementation: first split raw text into *segments* whenever a
    line matches the heading regex, then apply the sliding-window logic inside
    each segment.
    """

    # --------------- inline math sentinel pass ---------------
    # Replace LaTeX inline/block math with <MATH> so that queries containing
    # keywords like "sigma" or "E=mc^2" are not broken by token cleanup, while
    # still reducing token bloat.
    text = re.sub(r"\$\$?[^$]{1,100}?\$\$?", " <MATH> ", text)
    text = re.sub(r"\\\[[^\]]{1,200}?\\\]", " <MATH> ", text)

    segments: list[tuple[str, str]] = []  # (segment_text, heading)
    buf: list[str] = []
    current_heading = ""
    for line in text.splitlines():
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            if buf:
                segments.append(("\n".join(buf), current_heading))
            buf = [line]
            current_heading = stripped
        else:
            buf.append(line)
    if buf:
        segments.append(("\n".join(buf), current_heading))

    step = _MAX_TOKENS - _OVERLAP

    for seg_text, seg_heading in segments:
        tokens = _TOKENIZER.encode(seg_text, disallowed_special=())
        if not tokens:
            continue
        if len(tokens) <= _MAX_TOKENS:
            yield (_TOKENIZER.decode(tokens), seg_heading)
            continue
        for start in range(0, len(tokens), step):
            window = tokens[start : start + _MAX_TOKENS]
            if not window:
                break
            chunk_text = _TOKENIZER.decode(window)

            # Heuristic: skip bibliography-only chunks (same as before)
            lines = chunk_text.strip().splitlines()[:10]
            cite_hits = sum(1 for line in lines if _CITE_LINE_RE.match(line))
            if cite_hits >= 3:
                lowered = chunk_text.lower()
                keep_keywords = (
                    "conclusion" in lowered
                    or "discussion" in lowered
                    or "experiment" in lowered
                    or "results" in lowered
                )
                if not keep_keywords:
                    continue
            yield (chunk_text, seg_heading)
