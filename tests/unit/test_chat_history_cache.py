from __future__ import annotations

from pathlib import Path

import pytest

from services.chat.chat_history_cache import ChatHistoryCache


def test_chat_history_is_scoped_by_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ChatHistoryCache, "_cache_dir", str(tmp_path))

    ChatHistoryCache.save("session-a", "paper-1", [{"role": "user", "content": "from a"}])
    ChatHistoryCache.save("session-b", "paper-1", [{"role": "user", "content": "from b"}])

    assert ChatHistoryCache.load("session-a", "paper-1") == [{"role": "user", "content": "from a"}]
    assert ChatHistoryCache.load("session-b", "paper-1") == [{"role": "user", "content": "from b"}]


def test_chat_history_ignores_legacy_global_paper_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ChatHistoryCache, "_cache_dir", str(tmp_path))
    legacy_path = tmp_path / "paper-1.json"
    legacy_path.write_text(
        '{"turns": [{"role": "assistant", "content": "legacy"}]}',
        encoding="utf-8",
    )

    assert ChatHistoryCache.load("session-a", "paper-1") == []
