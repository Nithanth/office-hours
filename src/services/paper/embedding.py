"""Lightweight wrapper around Sentence-Transformers used by the RAG pipeline.
Keeps the model loaded as a global LRU-cached singleton so we do not pay the
initialisation cost more than once per process.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import cast

import numpy as np
from sentence_transformers import SentenceTransformer

__all__ = [
    "embed_texts",
    "embed_query",
]

MODEL_NAME = "all-MiniLM-L6-v2"


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


def embed_query(query: str) -> np.ndarray:
    """Convenience helper for single queries (returns 1-D float32 array)."""

    return cast(np.ndarray, embed_texts([query])[0])
