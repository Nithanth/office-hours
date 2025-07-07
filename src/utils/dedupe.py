"""Utilities for semantic deduplication of chat text against report text.

This module is **not wired in yet**.  It provides `deduplicate_chat` which can be
used later when generating report notes to drop chat sentences that are nearly
identical to sentences already present in the final report.

The heavy dependency (`sentence-transformers`) is optional; if it is not
installed the function will log a warning and return the chat text untouched.
"""
from __future__ import annotations

import logging
from typing import List

try:
    from sentence_transformers import SentenceTransformer, util  # type: ignore
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore
    util = None  # type: ignore

import nltk

_logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_model: SentenceTransformer | None = None  # type: ignore

def _get_model() -> "SentenceTransformer":  # type: ignore
    """Lazily load and cache the MiniLM embedding model."""
    global _model
    if _model is None:
        if SentenceTransformer is None:
            raise ImportError(
                "sentence-transformers not installed. Install it to enable deduplication."
            )
        _logger.info("[dedupe] Loading sentence-transformers model %s", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
    return _model  # type: ignore


def _sentences(text: str) -> List[str]:
    """Split text into sentences using NLTK punkt tokenizer (downloaded on demand)."""
    nltk.download("punkt", quiet=True)
    return nltk.sent_tokenize(text)


def deduplicate_chat(chat_text: str, report_text: str, threshold: float = 0.9) -> str:
    """Return `chat_text` with sentences removed that overlap strongly with the report.

    Args:
        chat_text: Full chat transcript after the report was generated.
        report_text: Cleaned textual content of the final report.
        threshold: Cosine-similarity threshold above which a chat sentence is
            considered redundant.  Range 0-1 (default 0.9).

    Returns:
        Filtered chat text containing only non-redundant sentences.
    """
    if SentenceTransformer is None or util is None:
        _logger.warning(
            "[dedupe] sentence-transformers unavailable; returning chat text unmodified.")
        return chat_text

    report_sents = _sentences(report_text)
    chat_sents = _sentences(chat_text)

    if not chat_sents or not report_sents:
        return chat_text

    model = _get_model()
    rep_emb = model.encode(report_sents, normalize_embeddings=True)
    chat_emb = model.encode(chat_sents, normalize_embeddings=True)

    keep: List[str] = []
    for sent, emb in zip(chat_sents, chat_emb):
        sim = util.cos_sim(emb, rep_emb).max().item()
        if sim < threshold:
            keep.append(sent)

    return " ".join(keep)
