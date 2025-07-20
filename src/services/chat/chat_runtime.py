"""Chat helpers: per-paper semaphores and prompt builder."""

import asyncio
import contextlib
import os
from collections import defaultdict

from loguru import logger

from config import config
from services.chat.chat_state import ChatState
from services.paper.text_extractor import TextExtractor

# Concurrency guard – max 3 concurrent generations per (session, paper)
_PaperKey = tuple[str, str]  # (session_id, paper_id)

_SEMAPHORES: dict[_PaperKey, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(3))


def get_semaphore(session_id: str, paper_id: str) -> asyncio.Semaphore:
    """Return the concurrency semaphore for this (session, paper) pair."""
    return _SEMAPHORES[(session_id, paper_id)]


# Prompt helper


async def build_prompt(
    chat_state: ChatState,
    paper_id: str,
    audience_type: str = "semi_technical",
    max_history: int = 10,
    excerpt_chars: int | None = None,
) -> str:
    """Construct prompt with recent chat + paper excerpt."""

    # ------------------------------------------------------------------
    # Determine excerpt length: prefer explicit argument, then app config,
    # finally environment variable (legacy). This avoids hard-coding a stale
    # default and lets users tweak the size via config.py or .env.
    # ------------------------------------------------------------------
    if excerpt_chars is None:
        excerpt_chars = config.EXCERPT_CHARS

        # Legacy fallback – still honour env override if set explicitly.
        env_override = os.getenv("EXCERPT_CHARS")
        if env_override is not None:
            with contextlib.suppress(ValueError):
                excerpt_chars = int(env_override)

    turns = chat_state.get_conversation(paper_id)[-max_history:]
    history_txt = "\n".join(f"{m.role}: {m.content}" for m in turns)

    # ------------------------------------------------------------------
    # RAG retrieval: if a vector index exists for this paper, use the user's
    # latest question to fetch the most relevant chunks and build *context*.
    # ------------------------------------------------------------------
    context_chunks = ""
    try:
        from services.paper import vector_store as _vs  # local import avoid cycles

        if _vs.index_exists(paper_id) and turns and turns[-1].role == "user":
            from services.paper.embedding import embed_query as _embed_q

            query_vec = _embed_q(turns[-1].content)
            matches = _vs.search(paper_id, query_vec, top_k=config.RAG_TOP_K)
            if matches:
                context_chunks = "\n\n".join(m["text"] for m in matches)
                logger.debug(
                    "rag_retrieval",
                    paper_id=paper_id,
                    chunks=len(matches),
                    chars=len(context_chunks),
                )
    except Exception as _e:  # pragma: no cover
        logger.warning("rag_failed", paper_id=paper_id, error=str(_e))

    excerpt = chat_state.paper_cache.setdefault(paper_id, {}).get("excerpt")

    # If retrieval returned results, prepend them **after** the abstract/excerpt so
    # the model first sees the global overview and then the supporting evidence.
    if context_chunks:
        excerpt = ((excerpt + "\n\n") if excerpt else "") + context_chunks

    # Full-text excerpt
    full_text_path = chat_state.paper_cache.get(paper_id, {}).get("full_text_path")
    if not full_text_path:
        # Should not happen if ensure_cached was run; attempt quick load.
        extractor = TextExtractor.instance()
        loaded_path = await extractor.load_full_text(paper_id)
        if loaded_path:
            full_text_path = loaded_path
            chat_state.paper_cache.setdefault(paper_id, {})["full_text_path"] = loaded_path
    if full_text_path and os.path.exists(full_text_path):
        need_reload = False
        if excerpt is None:
            need_reload = True  # never cached
        elif len(excerpt) < int(excerpt_chars * 0.8):
            # Cached excerpt is significantly shorter than desired target
            need_reload = True

        if need_reload:
            try:
                with open(full_text_path, encoding="utf-8") as fh:
                    excerpt = fh.read(excerpt_chars)
                chat_state.paper_cache.setdefault(paper_id, {})["excerpt"] = excerpt
                logger.debug(
                    "excerpt_loaded",
                    paper_id=paper_id,
                    chars=len(excerpt),
                    target=excerpt_chars,
                )
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("excerpt_load_failed", paper_id=paper_id, error=str(exc))
                excerpt = ""

    # Fallback to cached LLM summary when no excerpt or extremely short (<200 chars)
    if (not excerpt) or len(excerpt.strip()) < 200:
        summary_fallback = chat_state.paper_cache.get(paper_id, {}).get("summary")
        if summary_fallback:
            logger.debug("using_summary_fallback", paper_id=paper_id)
            excerpt = f"[LLM summary]\n{summary_fallback}"

    # Ensure excerpt is a string for subsequent operations
    if excerpt is None:
        excerpt = ""

    limited_ctx = len(excerpt.strip()) < 500
    if limited_ctx:
        logger.debug("limited_context", paper_id=paper_id, chars=len(excerpt))

    # Build system prompt: base persona loaded from template plus dynamic suffixes
    from prompts.prompt_builder import CHAT_SYSTEM_PROMPT  # local import

    sys_prompt = CHAT_SYSTEM_PROMPT

    # Append dynamic instructions specific to the chat context.
    sys_prompt += (
        " Answer the user's latest question clearly and concisely."
        " Where relevant, ground your answer in the paper excerpt silently—do NOT state 'based on the excerpt' or similar phrases."
    )

    # Adjust tone suffix based on audience type
    from prompts.prompt_builder import (
        AUDIENCE_PROMPT_MAP,
    )  # late import to avoid cycles

    if audience_type in AUDIENCE_PROMPT_MAP:
        sys_prompt += " " + AUDIENCE_PROMPT_MAP[audience_type]["system_suffix"]

    if limited_ctx:
        sys_prompt += (
            " Respond strictly based on the limited abstract/summary provided."
            " If the answer cannot be derived, respond with 'Information unavailable'."
        )

    prompt = (
        f"{sys_prompt}\n\n"
        f"Conversation:\n{history_txt}\n\n"
        f"Paper excerpt (first {excerpt_chars} chars):\n{excerpt}\n\nAssistant:"
    )
    return prompt
