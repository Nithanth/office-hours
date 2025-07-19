"""Per-paper vector store built on *lancedb*.

Each paper is indexed once (after full-text extraction).  We store the index
under ``.vector_cache/<sha>.lance`` so it is easy to ship / purge while keeping
it outside git.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import lancedb
import numpy as np
import tiktoken
from loguru import logger

from config import config
from services.paper.embedding import embed_texts

_VECTOR_ROOT = config.VECTOR_CACHE_DIR
_VECTOR_ROOT.mkdir(parents=True, exist_ok=True)

_TOKENIZER = tiktoken.get_encoding("cl100k_base")  # good default for GPT models
_MAX_TOKENS = config.RAG_CHUNK_TOKENS
_OVERLAP = config.RAG_CHUNK_OVERLAP

# Retrieval filtering thresholds (configurable via config.py)
_MIN_CHARS = config.RAG_MIN_CHARS           # ignore short chunks
_DISTANCE_THRESH = config.RAG_DISTANCE_L2    # max L2 distance to keep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_name(paper_id: str) -> str:
    """Return deterministic filename component from *paper_id* (no slashes)."""

    h = hashlib.sha1(paper_id.encode("utf-8")).hexdigest()[:16]
    return h


def index_path(paper_id: str) -> Path:
    return _VECTOR_ROOT / f"{_safe_name(paper_id)}.lance"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def index_exists(paper_id: str) -> bool:
    return index_path(paper_id).exists()


def save_index(paper_id: str, embeddings: np.ndarray, chunks: list[str]) -> None:
    """Write (or overwrite) the Lance index for *paper_id*."""

    if embeddings.shape[0] != len(chunks):
        raise ValueError("embeddings/chunks length mismatch")

    db = lancedb.connect(str(_VECTOR_ROOT))
    name = _safe_name(paper_id)

    # drop existing to avoid schema mismatch; safe because idempotent rebuilds
    if name in db.table_names():
        db.drop_table(name)

    data = [{"vector": vec.tolist(), "text": txt} for vec, txt in zip(embeddings, chunks)]
    db.create_table(name, data=data, mode="overwrite")
    logger.info("vector_index_built", paper_id=paper_id, chunks=len(chunks))


def search(paper_id: str, query_embedding: np.ndarray, *, top_k: int = config.RAG_TOP_K) -> list[dict]:
    """Return *top_k* records with highest similarity."""

    db = lancedb.connect(str(_VECTOR_ROOT))
    name = _safe_name(paper_id)
    if name not in db.table_names():
        return []

    table = db.open_table(name)
    # Ask for a few more to allow post-filter pruning
    candidates = table.search(query_embedding.tolist()).limit(top_k * 3).to_list()

    filtered = [
        r for r in candidates
        if len(r["text"]) >= _MIN_CHARS and r.get("_distance", 9e9) <= _DISTANCE_THRESH
    ]

    # lancedb returns `list[dict[str, Any]]` but type is not declared; cast for mypy.
    return cast(list[dict[str, Any]], filtered[:top_k])


def build_index_from_text(paper_id: str, text: str) -> None:
    """Helper that chunks *text*, embeds, and writes the index."""

    chunks = list(_chunk_text(text))
    embeddings = embed_texts(chunks)
    save_index(paper_id, embeddings, chunks)


# ---------------------------------------------------------------------------
# Internal – text chunking
# ---------------------------------------------------------------------------


def _chunk_text(text: str) -> Iterable[str]:
    """Yield overlapping text windows of `_MAX_TOKENS` tokens."""

    tokens = _TOKENIZER.encode(text, disallowed_special=())
    step = _MAX_TOKENS - _OVERLAP
    for start in range(0, len(tokens), step):
        window = tokens[start : start + _MAX_TOKENS]
        if not window:
            break
        yield _TOKENIZER.decode(window)
