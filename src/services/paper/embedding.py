"""Lightweight wrapper around Sentence-Transformers used by the RAG pipeline.
Keeps the model loaded as a global LRU-cached singleton so we do not pay the
initialisation cost more than once per process.
"""

from __future__ import annotations

import contextlib
import hashlib
import shelve
import time
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
from sentence_transformers import SentenceTransformer

import config as _cfg

__all__ = [
    "embed_texts",
    "embed_query",
]

MODEL_NAME = "all-MiniLM-L6-v2"

# ---------------------------------------------------------------------------
# Query-level embedding cache (avoids recomputing common queries)
# ---------------------------------------------------------------------------

_QUERY_VEC_CACHE: dict[str, tuple[float, np.ndarray]] = {}

# Keep items for 24 h to avoid unbounded memory growth
_CACHE_TTL_SEC: Final[int] = 60 * 60 * 24  # 24 hours

# Hard cap – drop oldest when we exceed this many cached queries
_MAX_CACHE_ITEMS: Final[int] = 10_000

# Optional on-disk cache – enabled when DISK_QUERY_CACHE=1
_DISK_CACHE_PATH = Path(_cfg.config.VECTOR_CACHE_DIR) / "query_cache.db"

# Use the broader Shelf type to satisfy mypy when we assign the concrete
# DbfilenameShelf returned by shelve.open().
_DISK_CACHE: shelve.Shelf[Any] | None = None


def _get_disk_cache() -> shelve.Shelf[Any] | None:
    if not _cfg.config.DISK_QUERY_CACHE:
        return None
    global _DISK_CACHE
    if _DISK_CACHE is None:
        _DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Intentionally keep the shelf handle open for the process lifetime.
        # Ruff SIM115 (require context manager) is suppressed because we want
        # a long-lived cache, not a scoped context.
        _DISK_CACHE = cast(
            shelve.Shelf[Any],
            shelve.open(str(_DISK_CACHE_PATH), writeback=False),  # noqa: SIM115
        )
    return _DISK_CACHE


@lru_cache(maxsize=1)
def _get_model(name: str = MODEL_NAME) -> SentenceTransformer:  # pragma: no cover
    """Load & cache the HuggingFace sentence-transformer model."""

    # device="cpu" keeps memory footprint predictable on servers without GPU.
    # The model is tiny (33 MB) so CPU inference is still sub-millisecond per
    # chunk; if CUDA is available SentenceTransformer will pick it up
    # automatically once we switch device="auto".
    return SentenceTransformer(name, device="cpu")


def _to_list(texts: Sequence[str] | str) -> list[str]:
    if isinstance(texts, str):
        return [texts]
    return list(texts)


def embed_texts(texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
    """Return L2-normalised embeddings for a batch of texts.

    Parameters
    ----------
    texts
        List/sequence of strings.
    batch_size
        Passed straight to SentenceTransformer.encode(). Larger batches speed
        up bulk indexing but use more memory.
    """

    # Allow env override for larger CPU batches when indexing
    batch_size = _cfg.config.EMB_BATCH_SIZE or batch_size

    model = _get_model()
    # mypy cannot infer numpy ndarrays returned by SentenceTransformer.encode.
    # Cast to np.ndarray to satisfy static checker.
    return cast(
        np.ndarray,
        model.encode(
            _to_list(texts),
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ),
    )


def _prune_cache() -> None:
    """Remove expired items and enforce the hard size limit."""

    now = time.time()

    # Drop expired
    expired_keys = [k for k, (ts, _) in _QUERY_VEC_CACHE.items() if now - ts > _CACHE_TTL_SEC]
    for k in expired_keys:
        _QUERY_VEC_CACHE.pop(k, None)

    # Drop LRU-ish overflow (oldest first) if we exceed size cap
    if len(_QUERY_VEC_CACHE) > _MAX_CACHE_ITEMS:
        # sort by timestamp ascending (oldest first)
        to_remove = sorted(_QUERY_VEC_CACHE.items(), key=lambda item: item[1][0])[
            : len(_QUERY_VEC_CACHE) - _MAX_CACHE_ITEMS
        ]
        for k, _ in to_remove:
            _QUERY_VEC_CACHE.pop(k, None)


def embed_query(query: str) -> np.ndarray:
    """Return L2-normalised embedding for *query*, using an in-process TTL cache."""

    key = hashlib.sha1(query.encode("utf-8")).hexdigest()
    now = time.time()

    cached = _QUERY_VEC_CACHE.get(key)
    if cached and now - cached[0] <= _CACHE_TTL_SEC:
        return cached[1]

    disk_cache = _get_disk_cache()
    if disk_cache is not None and key in disk_cache:
        ts, lst = disk_cache[key]
        if now - ts <= _CACHE_TTL_SEC:
            vec = np.asarray(lst, dtype=np.float32)
            _QUERY_VEC_CACHE[key] = (ts, vec)
            return vec

    vec = cast(np.ndarray, embed_texts([query])[0])
    _QUERY_VEC_CACHE[key] = (now, vec)
    if disk_cache is not None:
        with contextlib.suppress(Exception):
            disk_cache[key] = (now, vec.tolist())

    if len(_QUERY_VEC_CACHE) > _MAX_CACHE_ITEMS:
        _prune_cache()

    return vec
