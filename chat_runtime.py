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
    sys_prompt = os.getenv(
        "CHAT_SYSTEM_INSTRUCTIONS",
        "You are a helpful research assistant. Answer the latest user question using the context from the paper excerpt below when helpful.",
    ).strip()

    prompt = (
        f"{sys_prompt}\n\n"
        f"Conversation:\n{history_txt}\n\n"
        f"Paper excerpt (first {excerpt_chars} chars):\n{excerpt}\n\nAssistant:"
    )
    return prompt
