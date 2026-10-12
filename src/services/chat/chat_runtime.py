"""Chat helpers: per-paper semaphores and prompt builder."""

import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from config import config
from services.chat.chat_state import ChatState
from services.paper.text_extractor import TextExtractor


@dataclass
class PromptBundle:
    """Prompt plus auxiliary context for the chat stream.

    ``figure_paths`` holds extracted figure images for multimodal models and
    ``sources`` the numbered RAG chunks the prompt cites as ``[n]``; the stream
    endpoint emits ``sources`` as an SSE event so the UI can render citations.
    """

    prompt: str
    figure_paths: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


# Concurrency guard – max 3 concurrent generations per (session, paper)
_PaperKey = tuple[str, str]  # (session_id, paper_id)

_SEMAPHORES: dict[_PaperKey, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(3)
)


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
) -> PromptBundle:
    """Construct prompt with recent chat + paper excerpt.

    Returns a :class:`PromptBundle` carrying the prompt, any figure image
    paths for multimodal context, and the numbered retrieval sources the
    prompt instructs the model to cite as ``[n]``.
    """

    # ------------------------------------------------------------------
    # Determine excerpt length: prefer explicit argument, then app config,
    # finally environment variable (legacy). This avoids hard-coding a stale
    # default and lets users tweak the size via config.py or .env.
    # ------------------------------------------------------------------
    if excerpt_chars is None:
        excerpt_chars = config.EXCERPT_CHARS

    turns = chat_state.get_conversation(paper_id)[-max_history:]
    history_txt = "\n".join(f"{m.role}: {m.content}" for m in turns)

    # --------------------------------------------------------------
    # Global assistant (paper_id == "global") – no paper context / RAG.
    # --------------------------------------------------------------
    if paper_id == "global":
        from prompts.prompt_builder import AUDIENCE_PROMPT_MAP, CHAT_SYSTEM_PROMPT

        sys_prompt = (
            CHAT_SYSTEM_PROMPT
            + " Answer the user's latest question clearly and concisely."
        )
        if audience_type in AUDIENCE_PROMPT_MAP:
            sys_prompt += " " + AUDIENCE_PROMPT_MAP[audience_type]["system_suffix"]

        prompt = f"{sys_prompt}\n\nConversation:\n{history_txt}\n\nAssistant:"
        return PromptBundle(prompt=prompt)

    # ------------------------------------------------------------------
    # RAG retrieval: if a vector index exists for this paper, use the user's
    # latest question to fetch the most relevant chunks and build *context*.
    # ------------------------------------------------------------------
    context_chunks = ""
    figure_image_paths: list[str] = []  # paths to figure images for vision LLM
    sources: list[dict[str, Any]] = []  # numbered RAG chunks for inline [n] cites
    try:
        import importlib
        import sys

        # Use dynamic import so test stubs injected into sys.modules are honoured
        _vs = sys.modules.get("services.paper.vector_store")
        if _vs is None:
            _vs = importlib.import_module("services.paper.vector_store")

        if _vs.index_exists(paper_id) and turns and turns[-1].role == "user":
            from services.paper.embedding import embed_query as _embed_q

            user_q = turns[-1].content
            query_vec = _embed_q(user_q)
            matches = _vs.search(
                paper_id,
                query_vec,
                query_text=user_q,
                top_k=config.RAG_TOP_K,
            )
            if matches:
                # Number each chunk and prefix its section heading (if any) so
                # the model can cite sources inline as [n].
                from typing import cast

                def _format_row(idx: int, r: dict[str, Any]) -> str:
                    sec = r.get("section", "")
                    head = f"[{idx}] {sec}" if sec else f"[{idx}]"
                    return f"{head}\n{cast(str, r['text'])}"

                context_chunks = "\n\n".join(
                    _format_row(i + 1, m) for i, m in enumerate(matches)
                )
                sources = [
                    {
                        "n": i + 1,
                        "section": str(m.get("section") or ""),
                        "snippet": cast(str, m.get("text") or "")[:240],
                    }
                    for i, m in enumerate(matches)
                ]
                logger.debug(
                    "rag_retrieval",
                    paper_id=paper_id,
                    chunks=len(matches),
                    chars=len(context_chunks),
                )

                # If any retrieved chunks are figure descriptions, collect
                # the corresponding image paths for multimodal context.
                try:
                    from services.paper.figure_extractor import FigureExtractor

                    _fig_ext = FigureExtractor()
                    cached_figs = _fig_ext.get_cached_figures(paper_id)
                    if cached_figs:
                        for m in matches:
                            if m.get("is_figure") and cached_figs:
                                # Match figure by page number from chunk text
                                import re as _re

                                page_match = _re.search(
                                    r"page (\d+)", m.get("text", "")
                                )
                                if page_match:
                                    page_num = int(page_match.group(1))
                                    for fig in cached_figs:
                                        if fig.page_number == page_num:
                                            figure_image_paths.append(fig.image_path)
                                            break
                except Exception as _fig_err:
                    logger.debug("figure_image_lookup_failed", error=str(_fig_err))

    except Exception as _e:  # pragma: no cover
        logger.warning("rag_failed", paper_id=paper_id, error=str(_e))

    # ---------------- metadata load ----------------
    extractor = TextExtractor.instance()
    metadata_block: str = ""
    try:
        meta = await extractor.load_metadata(paper_id)
        if meta:
            parts: list[str] = []
            if title := meta.get("title"):
                parts.append(f"Title: {title}")
            if auth := meta.get("authors"):
                parts.append(f"Authors: {auth}")
            if yr := meta.get("year"):
                parts.append(f"Year: {yr}")
            metadata_block = "\n".join(parts)
    except Exception as _me:
        logger.debug("metadata_load_failed", paper_id=paper_id, err=str(_me))

    excerpt = chat_state.paper_cache.setdefault(paper_id, {}).get("excerpt")

    # Note: we defer combining *excerpt* and *context_chunks* until **after** the
    # optional full-text reload below; otherwise the reload can overwrite the
    # retrieval evidence we just fetched.

    # Full-text excerpt
    full_text_path = chat_state.paper_cache.get(paper_id, {}).get("full_text_path")
    if not full_text_path:
        # Should not happen if ensure_cached was run; attempt quick load.
        extractor = TextExtractor.instance()
        loaded_path = await extractor.load_full_text(paper_id)
        if loaded_path:
            full_text_path = loaded_path
            chat_state.paper_cache.setdefault(paper_id, {})[
                "full_text_path"
            ] = loaded_path
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

    # Finalise excerpt and append retrieval evidence (table/figure chunks, etc.).
    if excerpt is None:
        excerpt = ""

    if context_chunks:
        excerpt = ((excerpt + "\n\n") if excerpt else "") + context_chunks

    if metadata_block != "":
        excerpt = metadata_block + "\n\n" + excerpt

    limited_ctx = len(excerpt.strip()) < 500
    if limited_ctx:
        logger.debug("limited_context", paper_id=paper_id, chars=len(excerpt))

    # Build system prompt: base persona loaded from template plus dynamic suffixes
    from prompts.prompt_builder import CHAT_SYSTEM_PROMPT  # local import

    sys_prompt = CHAT_SYSTEM_PROMPT

    # Append dynamic instructions specific to the chat context.
    sys_prompt += " Answer the user's latest question clearly and concisely."
    if sources:
        sys_prompt += (
            " Use information from the numbered *Context* chunks below; when you"
            " draw on a chunk, cite it inline as [n] (e.g. [1], [3]) matching its"
            " number. Do not invent citations or cite numbers that do not appear."
        )
    else:
        sys_prompt += (
            " Use information from the *Context* below;"
            " do NOT mention the context explicitly."
        )
    sys_prompt += (
        " If the context contains lines starting with '=== TABLE',"
        " render them as markdown tables in your answer."
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

    prompt = f"{sys_prompt}\n\nConversation:\n{history_txt}\n\nContext:\n{excerpt}\n\nAssistant:"

    return PromptBundle(prompt=prompt, figure_paths=figure_image_paths, sources=sources)
