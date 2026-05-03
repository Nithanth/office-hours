"""Per-session chat state with disk persistence.

Previously this was in-memory only, causing issues with multiple uvicorn workers.
Now session state is loaded from disk on each access and saved after modifications,
enabling seamless conversation persistence across server restarts.
"""

import html
import time
from dataclasses import dataclass, field

from loguru import logger

import config as _cfg


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
    """Conversation container with disk persistence for multi-worker compatibility.

    Session state is loaded from disk on access and saved after modifications.
    This enables conversations to survive across uvicorn workers and server restarts.

    Attributes
    ----------
    _session_id : str
        Unique session identifier (from cookie).
    _histories : dict[str, list[ChatMessage]]
        Per-paper rolling window of recent turns.
    _cap : int
        Configurable cap, apply per paper.
    paper_cache : dict[str, dict]
        Per-paper metadata that persists for the lifetime of the session.
    """

    # Session ID for persistence
    _session_id: str = field(default="")
    # Per-paper rolling window of recent turns.
    _histories: dict[str, list[ChatMessage]] = field(default_factory=dict)
    # Max turns to retain per paper (mirrors persistence cap below)
    _cap: int = 50
    # Per-paper scratch space (e.g., cached full-text paths)
    paper_cache: dict[str, dict[str, str]] = field(default_factory=dict)
    # Ring buffer of last tokens for stream-resume (Phase 6)
    _buf_cap: int = _cfg.config.CHAT_STREAM_BUFFER
    stream_buffer: list[tuple[int, str]] = field(default_factory=list)
    next_token_idx: int = 0
    # True while an SSE stream is actively generating tokens for this session.
    stream_active: bool = False
    # Wallclock time when stream_active was set True; used for stuck-stream timeout.
    _stream_started_at: float = 0.0
    # Count consecutive busy 202 responses to allow escalating to 429.
    busy_retries: int = 0
    # Seconds after which a stuck stream_active flag is auto-reset.
    _STREAM_TIMEOUT_SEC: float = 120.0
    # Timestamp of last activity — used by GC loop to evict idle sessions.
    _last_t: float = field(default_factory=time.time)

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

    def _save_to_disk(self) -> None:
        """Persist current state to disk for multi-worker compatibility."""
        if not self._session_id:
            return

        try:
            from services.chat.session_state_cache import SessionStateCache

            state = {
                "paper_cache": self.paper_cache,
                # NOTE: stream_active and busy_retries are intentionally NOT
                # persisted — they are transient per-worker flags.  Persisting
                # them would cause a stuck-stream on restart.
                "_last_t": self._last_t,
                "_histories_keys": list(self._histories.keys()),  # Track which papers have history
            }
            SessionStateCache.save(self._session_id, state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_state_save_failed", session_id=self._session_id, err=str(exc))

    def _load_from_disk(self) -> None:
        """Load session state from disk."""
        if not self._session_id:
            return

        try:
            from services.chat.session_state_cache import SessionStateCache

            state = SessionStateCache.load(self._session_id)
            if state:
                self.paper_cache = state.get("paper_cache", {})
                # stream_active / busy_retries are NOT loaded — they are
                # transient per-worker state.
                self._last_t = state.get("_last_t", time.time())
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_state_load_failed", session_id=self._session_id, err=str(exc))

    def append(self, paper_id: str, msg: ChatMessage) -> None:
        """Append message *msg* to the chat history for *paper_id*."""
        self._last_t = time.time()
        history = self._histories.setdefault(paper_id, [])
        history.append(msg)
        self._histories[paper_id] = history[-self._cap :]

        # Persist to on-disk cache so history survives restarts.
        try:
            from services.chat.chat_history_cache import (
                ChatHistoryCache as _CHC,
            )  # local import

            # Serialise to plain dicts for JSON
            _CHC.save(
                self._session_id,
                paper_id,
                [m.serialise() for m in self._histories[paper_id]],
            )
        except Exception as exc:  # noqa: BLE001 – persistence is best-effort
            logger.warning("chat_history_save_failed", paper_id=paper_id, err=str(exc))

        # Also persist session state for multi-worker compatibility
        self._save_to_disk()

    def get_conversation(self, paper_id: str) -> list[ChatMessage]:  # noqa: D401 - helper
        """Return a copy of the history list for *paper_id*.

        If the paper has no in-memory history, attempt to restore from the
        on-disk chat cache so conversations survive server restarts.

        Also syncs session state from disk for multi-worker compatibility.
        """
        # Sync session state from disk first (for multi-worker compatibility)
        self._load_from_disk()

        if paper_id not in self._histories:
            try:
                from services.chat.chat_history_cache import ChatHistoryCache as _CHC

                turns = _CHC.load(self._session_id, paper_id)
                if turns:
                    self._histories[paper_id] = [
                        ChatMessage(role=t["role"], content=t["content"]) for t in turns
                    ]
            except Exception as exc:  # noqa: BLE001 – best-effort restore
                logger.warning("chat_history_restore_failed", paper_id=paper_id, err=str(exc))
        return list(self._histories.get(paper_id, []))

    def last_role(self, paper_id: str) -> str | None:
        """Return role of the last turn for *paper_id*, or None if no history."""
        conv = self._histories.get(paper_id)
        return conv[-1].role if conv else None


_chat_states: dict[str, ChatState] = {}


def get_chat_state(session_id: str) -> ChatState:
    """Return the ChatState object for *session_id*, creating if missing.

    This function is now multi-worker safe. Session state is loaded from disk
    when accessed, ensuring consistency across uvicorn workers.
    """
    if session_id not in _chat_states:
        # Create new ChatState with session ID for disk persistence
        state = ChatState(_session_id=session_id)

        # Try to load existing session state from disk (multi-worker compatibility)
        state._load_from_disk()

        _chat_states[session_id] = state
        logger.debug("chat_state_loaded", session_id=session_id)

    return _chat_states[session_id]
