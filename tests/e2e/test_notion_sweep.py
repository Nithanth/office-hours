"""E2e: the Notion comment sweep covers every synced page kind.

Papers were always swept; the regression this guards is topic pages
(living blog + notes) being silently skipped — a comment there sat
unanswered forever. Both page kinds must be visited by one pass.
"""

from __future__ import annotations

import pytest


class _FakeSession:
    """Answers the two selects _answer_notion_comments issues: first the
    SavedPaper page list, then the Topic page list (fixed order)."""

    def __init__(self, paper_rows, topic_rows):
        self._results = [paper_rows, topic_rows]
        self._calls = 0

    async def execute(self, _stmt):
        rows = self._results[self._calls]
        self._calls += 1

        class _R:
            def all(self):
                return rows

        return _R()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_sweep_covers_paper_and_topic_pages(monkeypatch):
    import api.routes.library as lib

    paper_rows = [("paper-1", "notion-paper-page")]
    topic_rows = [("topic-1", "notion-topic-page")]

    async def fake_session_scope():
        yield _FakeSession(paper_rows, topic_rows)

    monkeypatch.setattr(lib, "session_scope", fake_session_scope)

    integration = {"bot_id": "bot-9"}

    async def fake_load_integration(user_id):
        return integration, "secret-token"

    monkeypatch.setattr(lib, "_load_integration", fake_load_integration)

    calls: list[tuple[str, str]] = []

    async def fake_answer_paper(exporter, *, notion_page_id, paper_id, user_id, bot_id):
        calls.append(("paper", paper_id))
        return 1

    async def fake_answer_topic(exporter, *, notion_page_id, topic_id, user_id, bot_id):
        calls.append(("topic", topic_id))
        return 1

    import services.notion.library_sync as sync_mod

    monkeypatch.setattr(sync_mod, "answer_comments_for_paper", fake_answer_paper)
    monkeypatch.setattr(sync_mod, "answer_comments_for_topic", fake_answer_topic)

    class _FakeExporter:
        def __init__(self, token):
            self.closed = False

        async def close(self):
            self.closed = True

    # Imported inside the function — patch at the source module.
    import services.notion.notion_exporter as exporter_mod

    monkeypatch.setattr(exporter_mod, "NotionExporter", _FakeExporter)

    # Model-context plumbing is real but DB-free here — stub the owner
    # load so the pass stays hermetic.
    import services.core.model_prefs as prefs_mod

    async def fake_load_ctx(owner_id):
        return None

    monkeypatch.setattr(prefs_mod, "load_model_context_for_owner", fake_load_ctx)

    answered = await lib._answer_notion_comments("user-1")

    assert answered == 2
    assert calls == [("paper", "paper-1"), ("topic", "topic-1")]
