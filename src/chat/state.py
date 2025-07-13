"""Per-session in-memory chat history keyed by the session cookie."""

import html
import os
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ChatMessage:
    """A single chat turn."""

    role: str  # "user" | "assistant"
    content: str

    def serialise(self) -> dict[str, str]:  # noqa: D401 - small helper
        """Return a plain-dict representation suitable for JSON, logging, etc."""
        return {"role": self.role, "content": self.content}

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return (
            f"{self.role}: {html.escape(self.content[:60])}…"
            if len(self.content) > 60
            else f"{self.role}: {html.escape(self.content)}"
        )


@dataclass
class ChatState:
    """Conversation container limited to the most-recent N turns.

    Attributes
    ----------
    _histories : dict[str, list[ChatMessage]]
        Per-paper rolling window of recent turns.
    _cap : int
        Configurable cap, apply per paper.
    paper_cache : dict[str, dict]
        Per-paper metadata (e.g., {paper_id: {"full_text_path": str}}) that should
        persist for the lifetime of the session.
    """

    # Per-paper rolling window of recent turns.
    _histories: dict[str, list[ChatMessage]] = field(default_factory=dict)
    _cap: int = 20  # configurable later, apply per paper
    # Per-paper scratch space (e.g., cached full-text paths)
    paper_cache: dict[str, dict[str, str]] = field(default_factory=dict)
    # Ring buffer of last tokens for stream-resume (Phase 6)
    _buf_cap: int = int(os.getenv("CHAT_STREAM_BUFFER", "100"))
    stream_buffer: list[tuple[int, str]] = field(default_factory=list)
    next_token_idx: int = 0
    # True while an SSE stream is actively generating tokens for this session.
    stream_active: bool = False
    # Count consecutive busy 202 responses to allow escalating to 429.
    busy_retries: int = 0

    def reset_stream_buffer(self) -> None:
        """Call at the start of a new assistant stream."""
        self.stream_buffer.clear()
        self.next_token_idx = 0

    def push_token(self, token: str) -> None:
        """Add token to ring buffer keeping only last 100."""
        self.stream_buffer.append((self.next_token_idx, token))
        self.next_token_idx += 1
        # respect configurable cap
        if len(self.stream_buffer) > self._buf_cap:
            self.stream_buffer = self.stream_buffer[-self._buf_cap :]

    # Public helpers – per-paper chat history

    def append(self, paper_id: str, msg: ChatMessage) -> None:
        """Append message *msg* to the chat history for *paper_id*."""
        history = self._histories.setdefault(paper_id, [])
        history.append(msg)
        self._histories[paper_id] = history[-self._cap :]

    def get_conversation(self, paper_id: str) -> list[ChatMessage]:  # noqa: D401 - helper
        """Return a copy of the history list for *paper_id*."""
        return list(self._histories.get(paper_id, []))

    def last_role(self, paper_id: str) -> str | None:
        """Return role of the last turn for *paper_id*, or None if no history."""
        conv = self._histories.get(paper_id)
        return conv[-1].role if conv else None


_chat_states: dict[str, ChatState] = {}

try:  # Sched affinity not available on macOS; fallback gracefully
    _cpu_count = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 - any failure → assume single worker
    _cpu_count = os.cpu_count() or 1

_uvicorn_workers_env = os.getenv("UVICORN_WORKERS")
try:
    _declared_workers = int(_uvicorn_workers_env) if _uvicorn_workers_env else 1
except ValueError:
    _declared_workers = 1

from loguru import logger

if max(_cpu_count, _declared_workers) > 1:
    logger.warning(
        "chatstate_multiple_workers",
        cpus=_cpu_count,
        uvicorn_workers=_declared_workers,
        note="Conversations not shared across workers; switch to Redis later.",
    )


def get_chat_state(session_id: str) -> ChatState:
    """Return the ChatState object for *session_id*, creating if missing."""
    if session_id not in _chat_states:
        _chat_states[session_id] = ChatState()
    return _chat_states[session_id]
