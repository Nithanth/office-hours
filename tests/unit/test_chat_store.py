"""Unit tests for chat state storage backends."""

import tempfile

import pytest

from services.chat.stores.base import ChatStore
from services.chat.stores.file_store import FileChatStore


@pytest.fixture
def file_store():
    """Create a FileChatStore in a temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield FileChatStore(base_dir=tmp)


class TestFileChatStore:
    """Test the file-backed chat store."""

    def test_save_and_load_history(self, file_store: FileChatStore):
        turns = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        file_store.save_history("session1", "paper_abc", turns)
        loaded = file_store.load_history("session1", "paper_abc")
        assert loaded == turns

    def test_load_empty_history(self, file_store: FileChatStore):
        loaded = file_store.load_history("no_session", "no_paper")
        assert loaded == []

    def test_history_cap(self, file_store: FileChatStore):
        """History is capped at 50 turns."""
        turns = [{"role": "user", "content": f"msg {i}"} for i in range(60)]
        file_store.save_history("sess", "paper", turns)
        loaded = file_store.load_history("sess", "paper")
        assert len(loaded) == 50
        assert loaded[-1]["content"] == "msg 59"

    def test_delete_history(self, file_store: FileChatStore):
        file_store.save_history("sess", "paper", [{"role": "user", "content": "hi"}])
        file_store.delete_history("sess", "paper")
        assert file_store.load_history("sess", "paper") == []

    def test_save_and_load_session(self, file_store: FileChatStore):
        state = {"paper_cache": {"paper_a": {"path": "/tmp/a.txt"}}, "_last_t": 123.0}
        file_store.save_session("sess1", state)
        loaded = file_store.load_session("sess1")
        assert loaded is not None
        assert loaded["paper_cache"]["paper_a"]["path"] == "/tmp/a.txt"

    def test_load_missing_session(self, file_store: FileChatStore):
        assert file_store.load_session("ghost") is None

    def test_delete_session(self, file_store: FileChatStore):
        file_store.save_session("sess", {"x": 1})
        file_store.save_history("sess", "paper1", [{"role": "user", "content": "hi"}])
        file_store.delete_session("sess")
        assert file_store.load_session("sess") is None
        assert file_store.load_history("sess", "paper1") == []

    def test_list_sessions(self, file_store: FileChatStore):
        file_store.save_session("alpha", {"a": 1})
        file_store.save_session("beta", {"b": 2})
        sessions = file_store.list_sessions()
        assert set(sessions) == {"alpha", "beta"}

    def test_clear_all(self, file_store: FileChatStore):
        file_store.save_session("x", {"v": 1})
        file_store.save_session("y", {"v": 2})
        count = file_store.clear_all()
        assert count == 2
        assert file_store.list_sessions() == []

    def test_implements_protocol(self, file_store: FileChatStore):
        """FileChatStore is a proper ChatStore subclass."""
        assert isinstance(file_store, ChatStore)
