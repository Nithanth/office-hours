"""Per-paper vector store built on *lancedb*.

Each paper is indexed once (after full-text extraction).  We store the index
under ``.vector_cache/<sha>.lance`` so it is easy to ship / purge while keeping
it outside git.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import lancedb
import numpy as np
import tiktoken
from loguru import logger
from sentence_transformers import CrossEncoder

from config import config
from services.paper.embedding import embed_texts

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

# ---------------------------------------------------------------------------
# Optional cross-encoder rerank (query ⊕ chunk for fine-grained scoring)
# ---------------------------------------------------------------------------

_CE_MODEL: CrossEncoder | None = None
_CE_POOL = config.CE_RERANK_POOL  # how many candidates to rerank
_CE_PAIR_CACHE: dict[
    tuple[str, str], float
] = {}  # Simple in-process cache for (query, chunk_text) → ce_score


def _load_ce() -> CrossEncoder:
    """Lazy-load the cross-encoder singleton."""

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
        for idx, score in zip(missing_indices, new_scores):
            scores[idx] = float(score)
            _CE_PAIR_CACHE[missing_pairs[idx]] = float(score)

    for h, s in zip(hits, scores):
        h["_ce_score"] = s
    return sorted(hits, key=lambda r: r["_ce_score"], reverse=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_name(paper_id: str) -> str:
    """Return deterministic filename component from *paper_id* (no slashes)."""

    h = hashlib.sha1(paper_id.encode("utf-8")).hexdigest()[:16]
    return h


def index_path(paper_id: str) -> Path:
    """Return absolute path to Lance index for *paper_id*."""
    return Path(_VECTOR_ROOT) / f"{_safe_name(paper_id)}.lance"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def index_exists(paper_id: str) -> bool:
    return index_path(paper_id).exists()


def save_index(
    paper_id: str,
    embeddings: np.ndarray,
    chunks: list[str],
    *,
    headings: list[str] | None = None,
) -> None:
    """Write (or overwrite) the Lance index for *paper_id*."""

    if embeddings.shape[0] != len(chunks):
        raise ValueError("embeddings/chunks length mismatch")

    db = lancedb.connect(str(_VECTOR_ROOT))
    name = _safe_name(paper_id)

    # drop existing to avoid schema mismatch; safe because idempotent rebuilds
    if name in db.table_names():
        db.drop_table(name)

    data: list[dict[str, Any]] = []
    for idx, (vec, txt) in enumerate(zip(embeddings, chunks)):
        stripped = txt.lstrip()
        heading_val = headings[idx] if headings else ""
        meta = {
            "vector": vec.tolist(),
            "text": txt,
            "is_table": stripped.startswith("=== TABLE"),
            "is_figure": stripped.startswith("=== FIGURE"),
            "section": heading_val,
        }
        data.append(meta)
    db.create_table(name, data=data, mode="overwrite")
    logger.info("vector_index_built", paper_id=paper_id, chunks=len(chunks))


def search(
    paper_id: str,
    query_embedding: np.ndarray,
    *,
    query_text: str = "",
    top_k: int = 12,
) -> list[dict]:
    """Return *top_k* records most relevant to *query_text*.

    Two-phase filtering:
    1. Strict → distance + length thresholds.
    2. Relaxed → apply only length, then boost rows whose content shares
       keywords with *query_text* (helps surface `=== TABLE` chunks when the
       user explicitly mentions a metric or "table").
    """

    db = lancedb.connect(str(_VECTOR_ROOT))
    name = _safe_name(paper_id)
    if name not in db.table_names():
        return []

    # Start elapsed-time measurement for search + optional rerank
    t0 = time.perf_counter()

    # LanceDB >=0.4 removed the `mode` kwarg.  Fall back gracefully.
    try:
        table = db.open_table(name, mode="r")  # old API (<=0.3)
    except TypeError:
        table = db.open_table(name)  # new API – defaults to read-only

    # Fetch a wider pool to avoid missing relevant sections that are slightly
    # further down the similarity ranking.
    # Compatibility: older lancedb versions expose .to_pandas() but not .to_list().
    try:
        candidates = (
            table.search(query_embedding.tolist()).limit(top_k * _SEARCH_MULTIPLIER).to_list()
        )
    except AttributeError:
        try:
            candidates = (
                table.search(query_embedding.tolist())
                .limit(top_k * _SEARCH_MULTIPLIER)
                .to_pandas()
                .to_dict("records")
            )
        except AttributeError:
            # Final fallback – get N rows and convert via arrow / pylist.
            arrow_tbl = (
                table.search(query_embedding.tolist()).limit(top_k * _SEARCH_MULTIPLIER).to_arrow()
            )
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
        logger.debug("rag_phase", phase="ce_rerank", kept=len(pool))

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
        logger.debug("rag_phase", phase="strict", kept=len(strict), search_ms=t_search_ms)
        return cast(list[dict[str, Any]], strict[:top_k])

    # Second pass – relax distance threshold, keep only length filter so we
    # still surface something relevant (best-effort).
    relaxed = [r for r in candidates if len(r["text"]) >= _MIN_CHARS or _is_special(r["text"])]
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
            "rag_phase", phase="relaxed_kw", kept=len(relaxed[:top_k]), search_ms=t_search_ms
        )
        return cast(list[dict[str, Any]], relaxed[:top_k])

    # Last resort – return the highest-ranked chunks regardless of distance so the
    # caller can still display *something* (better than an empty chat response).
    if candidates:
        logger.debug(
            "rag_phase", phase="fallback", kept=len(candidates[:top_k]), search_ms=t_search_ms
        )
        return cast(list[dict[str, Any]], candidates[:top_k])

    return []


def build_index_from_text(paper_id: str, text: str) -> None:
    """Chunk *text*, embed each chunk, and persist the LanceDB index."""

    chunk_tuples = list(_chunk_text(text))  # (chunk_text, heading)
    if not chunk_tuples:
        return

    chunk_texts = [c for c, _ in chunk_tuples]
    headings = [h for _, h in chunk_tuples]

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
