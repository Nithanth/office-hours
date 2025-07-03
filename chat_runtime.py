"""chat_runtime.py
Utilities shared by chat endpoints: per-paper semaphores and helper
prompt assembly.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict

from chat_state import ChatState
from services.text_extractor import TextExtractor

# ---------------------------------------------------------------------------
# Concurrency guard: at most 3 concurrent generations per (session, paper).
# ---------------------------------------------------------------------------

_PaperKey = tuple[str, str]  # (session_id, paper_id)

_SEMAPHORES: dict[_PaperKey, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(3))


def get_semaphore(session_id: str, paper_id: str) -> asyncio.Semaphore:
    """Return the concurrency semaphore for this (session, paper) pair."""
    return _SEMAPHORES[(session_id, paper_id)]


# ---------------------------------------------------------------------------
# Prompt helper
# ---------------------------------------------------------------------------


async def build_prompt(
    chat_state: ChatState,
    paper_id: str,
    audience_type: str = "semi_technical",
    max_history: int = 10,
    excerpt_chars: int = int(os.getenv("EXCERPT_CHARS", "2000")),
) -> str:
    """Construct prompt with recent chat + paper excerpt."""

    # Recent history last max_history turns for this paper
    turns = chat_state.get_conversation(paper_id)[-max_history:]
    history_txt = "\n".join(f"{m.role}: {m.content}" for m in turns)

    # Full-text excerpt
    full_text_path = chat_state.paper_cache.get(paper_id, {}).get("full_text_path")
    if not full_text_path:
        # Should not happen if ensure_cached was run; attempt quick load.
        extractor = TextExtractor.instance()
        loaded_path = await extractor.load_full_text(paper_id)
        if loaded_path:
            full_text_path = loaded_path
            chat_state.paper_cache.setdefault(paper_id, {})["full_text_path"] = loaded_path
    excerpt = chat_state.paper_cache.setdefault(paper_id, {}).get("excerpt")
    if excerpt is None and full_text_path and os.path.exists(full_text_path):
        try:
            with open(full_text_path, encoding="utf-8") as fh:
                excerpt = fh.read(excerpt_chars)
            # cache excerpt to avoid disk I/O next turn
            chat_state.paper_cache[paper_id]["excerpt"] = excerpt
        except Exception:
            excerpt = ""

    # Fallback to cached LLM summary when no excerpt or extremely short (<200 chars)
    if (not excerpt) or len(excerpt.strip()) < 200:
        summary_fallback = chat_state.paper_cache.get(paper_id, {}).get("summary")
        if summary_fallback:
            excerpt = f"[LLM summary]\n{summary_fallback}"

    # Ensure excerpt is a string for subsequent operations
    if excerpt is None:
        excerpt = ""

    limited_ctx = len(excerpt.strip()) < 500

    # Build system prompt: base persona + concise instruction
    from utils.prompt_templates import DEFAULT_SYSTEM_PROMPT  # local import

    sys_prompt = (
        DEFAULT_SYSTEM_PROMPT
        + " You are assisting in a live Q&A about a specific research paper."
        + " Answer the user's latest question clearly and concisely."
        + " Where relevant, ground your answer in the paper excerpt silently—do NOT state 'based on the excerpt' or similar phrases."
    )

    # Adjust tone suffix based on audience type
    from utils.prompt_templates import (
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
