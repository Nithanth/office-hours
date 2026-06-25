"""Unit tests for ChatStore factory and Redis store basics."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.chat.stores.factory import get_store, reset_store


class TestFactory:
    """Tests for the chat store factory."""

    def setup_method(self):
        reset_store()

    def teardown_method(self):
        reset_store()

    def test_default_backend_is_file(self):
        """Without config, factory returns FileChatStore."""
        store = get_store()
        from services.chat.stores.file_store import FileChatStore

        assert isinstance(store, FileChatStore)

    def test_singleton_returns_same_instance(self):
        """Repeated calls return the same object."""
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2

    def test_reset_clears_singleton(self):
        """reset_store() forces a new instance on next call."""
        s1 = get_store()
        reset_store()
        s2 = get_store()
        # New instance (may be equal in value but different object)
        assert s2 is not s1

    def test_redis_backend_requires_package(self):
        """Setting CHAT_STORE_BACKEND=redis raises ImportError without redis pkg."""
        import config

        orig = getattr(config.config, "CHAT_STORE_BACKEND", "file")
        try:
            config.config.CHAT_STORE_BACKEND = "redis"
            config.config.REDIS_URL = "redis://localhost:6379/0"
            reset_store()
            try:
                store = get_store()
                # If redis IS installed, verify it returns RedisChatStore
                from services.chat.stores.redis_store import RedisChatStore
                assert isinstance(store, RedisChatStore)
            except ImportError:
                # Expected when redis package not installed
                pass
        finally:
            config.config.CHAT_STORE_BACKEND = orig
            reset_store()


class TestFileStoreBasics:
    """Functional tests for FileChatStore (the default backend)."""

    def setup_method(self):
        reset_store()

    def teardown_method(self):
        reset_store()

    def test_save_and_load_history(self):
        store = get_store()
        turns = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        store.save_history("sess1", "paper1", turns)
        history = store.load_history("sess1", "paper1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "hi there"

    def test_separate_sessions(self):
        store = get_store()
        store.save_history("a", "p1", [{"role": "user", "content": "msg a"}])
        store.save_history("b", "p1", [{"role": "user", "content": "msg b"}])
        assert len(store.load_history("a", "p1")) == 1
        assert len(store.load_history("b", "p1")) == 1
        assert store.load_history("a", "p1")[0]["content"] == "msg a"

    def test_nonexistent_session_returns_empty(self):
        store = get_store()
        assert store.load_history("nonexistent", "p1") == []

    def test_delete_history(self):
        store = get_store()
        store.save_history("sess", "p1", [{"role": "user", "content": "test"}])
        store.delete_history("sess", "p1")
        assert store.load_history("sess", "p1") == []

    def test_session_metadata(self):
        store = get_store()
        store.save_session("s1", {"paper_cache": {"p1": "data"}, "ts": 123})
        loaded = store.load_session("s1")
        assert loaded is not None
        assert loaded["paper_cache"]["p1"] == "data"

    def test_list_sessions(self):
        store = get_store()
        store.save_session("s1", {"x": 1})
        store.save_session("s2", {"y": 2})
        sessions = store.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions
