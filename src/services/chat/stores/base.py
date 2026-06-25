"""Abstract interface for chat state persistence backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChatStore(ABC):
    """Protocol for chat state persistence.

    Implementations must handle:
    - Per-paper chat history (list of turn dicts)
    - Per-session metadata (paper_cache, timestamps, etc.)

    All methods are synchronous for simplicity (the file backend is sync,
    and Redis calls are fast enough to not warrant async in this context).
    """

    # ------------------------------------------------------------------
    # Chat history (per-session, per-paper)
    # ------------------------------------------------------------------

    @abstractmethod
    def save_history(
        self, session_id: str, paper_id: str, turns: list[dict[str, str]]
    ) -> None:
        """Persist chat turns for a given session+paper pair."""

    @abstractmethod
    def load_history(
        self, session_id: str, paper_id: str
    ) -> list[dict[str, str]]:
        """Load chat turns. Returns empty list if none exist."""

    @abstractmethod
    def delete_history(self, session_id: str, paper_id: str) -> None:
        """Delete chat history for a specific paper in a session."""

    # ------------------------------------------------------------------
    # Session state (per-session metadata)
    # ------------------------------------------------------------------

    @abstractmethod
    def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        """Persist session-level metadata (paper_cache, timestamps, etc.)."""

    @abstractmethod
    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """Load session metadata. Returns None if not found."""

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Delete all state for a session (history + metadata)."""

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """Return all known session IDs."""

    def clear_all(self) -> int:
        """Clear all stored data. Returns count of sessions removed."""
        sessions = self.list_sessions()
        for sid in sessions:
            self.delete_session(sid)
        return len(sessions)
