"""Notion topic mirror — workspace topics into the Topics DB."""

from __future__ import annotations

import hashlib

import pytest

from services.notion.library_sync import (
    _topic_body_markdown,
    sync_library,
    sync_topics,
)


def _topic(**overrides):
    base = {
        "id": "t-1",
        "title": "Reasoning models",
        "kind": "subject",
        "seed_query": "reasoning models",
        "created_date": "2026-10-15",
        "blog_markdown": "# Take\n\nBlog body.",
        "notes_markdown": "- note one",
        "notion_page_id": None,
        "notion_body_hash": None,
    }
    base.update(overrides)
    return base


class _FakeExporter:
    """Records the calls sync_topics / sync_library make."""

    def __init__(self, page_empty=True):
        self.page_empty = page_empty
        self.created_rows: list[tuple[str, dict]] = []
        self.updated_props: list[tuple[str, dict]] = []
        self.appended: list[tuple[str, list]] = []
        self.replaced: list[tuple[str, str]] = []
        self.calls = 0

    async def create_database_row(self, db_id, props):
        self.calls += 1
        self.created_rows.append((db_id, props))
        return f"page-{len(self.created_rows)}"

    async def update_page_properties(self, page_id, props):
        self.calls += 1
        self.updated_props.append((page_id, props))

    async def page_is_empty(self, page_id):
        return self.page_empty

    async def append_blocks(self, page_id, blocks):
        self.calls += 1
        self.appended.append((page_id, blocks))

    async def replace_page_content(self, page_id, markdown):
        self.calls += 1
        self.replaced.append((page_id, markdown))

    async def get_page_status(self, page_id):
        return None


def _synced_hash(topic):
    return hashlib.sha256(_topic_body_markdown(topic).encode()).hexdigest()


@pytest.mark.asyncio
async def test_creates_row_and_writes_body_when_new():
    ex = _FakeExporter()
    stats = await sync_topics(ex, topics_db_id="db-t", topics=[_topic()])
    assert stats["topics_created"] == 1
    assert stats["bodies_written"] == 1
    assert stats["topic_page_ids"]["t-1"] == "page-1"
    assert stats["body_hashes"]["t-1"] == _synced_hash(_topic())
    # First write on an empty page appends, doesn't replace.
    assert len(ex.appended) == 1 and not ex.replaced


@pytest.mark.asyncio
async def test_skips_body_when_hash_unchanged():
    t = _topic(notion_page_id="page-x", notion_body_hash=_synced_hash(_topic()))
    ex = _FakeExporter()
    stats = await sync_topics(ex, topics_db_id="db-t", topics=[t])
    assert stats["topics_updated"] == 1
    assert stats["bodies_skipped"] == 1
    assert not ex.appended and not ex.replaced
    assert "t-1" not in stats["body_hashes"]


@pytest.mark.asyncio
async def test_replaces_body_on_existing_page_when_content_changed():
    t = _topic(notion_page_id="page-x", notion_body_hash="stale")
    ex = _FakeExporter(page_empty=False)
    stats = await sync_topics(ex, topics_db_id="db-t", topics=[t])
    assert stats["bodies_written"] == 1
    assert ex.replaced and "## Blog" in ex.replaced[0][1]
    assert ex.replaced[0][1].count("Blog body.") == 1


@pytest.mark.asyncio
async def test_body_falls_back_to_placeholders_when_empty():
    t = _topic(blog_markdown=None, notes_markdown=None)
    body = _topic_body_markdown(t)
    assert "_No blog generated yet._" in body
    assert "_No notes generated yet._" in body
    assert "subject topic" in body


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_others():
    class _Flaky(_FakeExporter):
        async def create_database_row(self, db_id, props):
            raise RuntimeError("boom")

    ex = _Flaky()
    stats = await sync_topics(
        ex,
        topics_db_id="db-t",
        topics=[_topic(), _topic(id="t-2", title="ok", notion_page_id="p2")],
    )
    assert stats["failed"] == 1
    assert "t-2" in stats["topic_page_ids"]


@pytest.mark.asyncio
async def test_sync_library_prefers_topic_page_relation():
    """Saved paper's Topic relation points at the topic page, not the
    report row, when the report belongs to a synced topic."""
    ex = _FakeExporter()
    paper = {
        "paper_id": "p1",
        "title": "Paper",
        "url": "https://arxiv.org/abs/1",
        "authors": {"list": ["A"]},
        "status": "to_read",
        "report_id": "r1",
        "notion_page_id": None,
        "notion_synced_status": None,
        "notion_chat_synced": 0,
        "saved_date": "2026-10-15",
        "chat_messages": [],
    }
    reports = {
        "r1": {
            "title": "Rep",
            "original_query": "q",
            "created_date": "2026-10-15",
            "notion_page_id": None,
            "content": None,
        }
    }
    stats = await sync_library(
        ex,
        papers_db_id="db-p",
        topics_db_id="db-t",
        papers=[paper],
        reports=reports,
        topic_page_by_report_id={"r1": "topic-page-99"},
    )
    # The created paper row's Topic relation is the topic page.
    paper_row = [c for c in ex.created_rows if c[0] == "db-p"][0]
    assert paper_row[1]["Topic"] == {"relation": [{"id": "topic-page-99"}]}
    # Report row still created in the Topics DB (snapshot rows unchanged).
    assert any(c[0] == "db-t" for c in ex.created_rows)
    assert stats["topic_page_ids"]["r1"] == "page-1"
