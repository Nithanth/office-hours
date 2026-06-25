"""Chat state storage backends.

Provides a pluggable backend for persisting chat history and session state.
Configure via the ``CHAT_STORE_BACKEND`` environment variable:

- ``"file"`` (default): JSON files on disk with advisory locking
- ``"redis"``: Redis-backed storage for multi-instance deployments

Usage::

    from services.chat.stores import get_store

    store = get_store()
    store.save_history(session_id, paper_id, turns)
    turns = store.load_history(session_id, paper_id)
"""

from services.chat.stores.base import ChatStore
from services.chat.stores.factory import get_store

__all__ = ["ChatStore", "get_store"]
