"""Utility functions for deduplicating chat messages against the generated report."""

from typing import cast

from loguru import logger
from sentence_transformers import SentenceTransformer, util

from config import config

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_THRESHOLD = config.CHAT_DEDUPE_SIM_THRESHOLD
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Lazily load and cache the MiniLM embedding model."""
    global _model
    if _model is None:
        logger.bind(component="Dedupe").info(
            "loading sentence-transformers model", model=_MODEL_NAME
        )
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _sentences(text: str) -> list[str]:
    """Return a list of sentences.

    Primary approach = NLTK *punkt*.  If the resource is missing and cannot be
    fetched (e.g. offline container), fall back to a naive regex splitter that
    is "good enough" for similarity‐dedupe purposes.
    """

    try:
        from nltk.tokenize import sent_tokenize  # lazy import so nltk is optional

        # Attempt to use punkt; don't auto-download to avoid blocking.
        return cast(list[str], sent_tokenize(text))
    except LookupError as err:
        logger.warning("punkt_missing_fallback", error=str(err))

    # ------------------------------------------------------------------
    # Fallback splitter when NLTK punkt is unavailable
    #   • Primary delimiter: end-of-sentence punctuation (. ! ?) followed by
    #     whitespace *or* newline characters.
    #   • Secondary delimiter: common bullet/number list prefixes so each bullet
    #     is treated as its own "sentence" for embedding similarity.
    # ------------------------------------------------------------------
    import re

    bullet_prefix_pat = r"\s*(?:[•◦▪\-*]|\d+[.)])\s+"
    delim_regex = rf"(?<=[.!?])\s+|\n+|{bullet_prefix_pat}"
    rough = re.split(delim_regex, text.strip())
    return [s for s in rough if s]


def deduplicate_chat(
    chat_text: str, report_text: str, threshold: float = _DEFAULT_THRESHOLD
) -> str:
    """Return `chat_text` with sentences removed that overlap strongly with the report.

    Args:
        chat_text: Full chat transcript after the report was generated.
        report_text: Cleaned textual content of the final report.
        threshold: Cosine-similarity threshold above which a chat sentence is
            considered redundant.  Range 0-1 (default 0.9).

    Returns:
        Filtered chat text containing only non-redundant sentences.
    """
    # Dependencies are assumed present; proceed directly.

    report_sents = _sentences(report_text)
    chat_sents = _sentences(chat_text)

    if not chat_sents or not report_sents:
        return chat_text

    model = _get_model()
    rep_emb = model.encode(report_sents, normalize_embeddings=True)
    chat_emb = model.encode(chat_sents, normalize_embeddings=True)

    keep: list[str] = []
    for sent, emb in zip(chat_sents, chat_emb):
        sim = util.cos_sim(emb, rep_emb).max().item()
        if sim < threshold:
            keep.append(sent)

    logger.debug(
        "chat_dedupe", total=len(chat_sents), kept=len(keep), removed=len(chat_sents) - len(keep), threshold=threshold
    )

    return " ".join(keep)
