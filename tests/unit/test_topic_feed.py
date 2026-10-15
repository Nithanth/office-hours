"""Unit tests for the topic feed service.

Provider calls are mocked at ``fetch_feed_papers``/``_fetch_candidates_sync``
boundaries — these tests cover feed bookkeeping (last_feed_check, source='feed',
dedup) and the per-kind fetch mapping, not the providers themselves.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

aiosqlite = pytest.importorskip(
    "aiosqlite", reason="aiosqlite is required for SQLite async tests"
)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from models.paper import Paper  # noqa: E402
from services.persistence.database import Base  # noqa: E402
from services.persistence.models import Topic, TopicPaper  # noqa: E402
from services.persistence.repositories import add_topic_papers  # noqa: E402
from services.topics import feed  # noqa: E402


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _topic(**kw) -> Topic:
    return Topic(
        owner_id="sess:o",
        kind=kw.pop("kind", "subject"),
        canonical_key=kw.pop("canonical_key", "subject:inr"),
        title=kw.pop("title", "INR"),
        seed_query=kw.pop("seed_query", "implicit neural representations"),
        **kw,
    )


def _paper(pid: str, title: str = "P") -> Paper:
    return Paper(id=pid, title=title, url=f"https://arxiv.org/abs/{pid}")


class TestFeedTopic:
    async def test_adds_feed_papers_and_stamps_check(
        self, db_session: AsyncSession, monkeypatch
    ):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()

        async def fake_fetch(t, *, since):
            return [_paper("2501.0001", "New INR paper")]

        monkeypatch.setattr(feed, "fetch_feed_papers", fake_fetch)

        added = await feed.feed_topic(db_session, topic)
        assert added == 1
        assert topic.last_feed_check is not None

        row = (
            await db_session.execute(
                select(TopicPaper).where(TopicPaper.topic_id == topic.id)
            )
        ).scalar_one()
        assert row.source == "feed"
        assert row.paper_id == "2501.0001"

    async def test_dedup_on_repeat_poll(self, db_session: AsyncSession, monkeypatch):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await add_topic_papers(
            db_session, topic_id=topic.id, papers=[_paper("2501.0001")], source="seed"
        )
        await db_session.commit()

        async def fake_fetch(t, *, since):
            return [_paper("2501.0001"), _paper("2501.0002")]

        monkeypatch.setattr(feed, "fetch_feed_papers", fake_fetch)
        added = await feed.feed_topic(db_session, topic)
        assert added == 1  # only the genuinely new paper

    async def test_uses_last_check_as_since(
        self, db_session: AsyncSession, monkeypatch
    ):
        topic = _topic()
        topic.last_feed_check = datetime(2026, 10, 1)
        db_session.add(topic)
        await db_session.commit()
        seen_since: list[datetime] = []

        async def fake_fetch(t, *, since):
            seen_since.append(since)
            return []

        monkeypatch.setattr(feed, "fetch_feed_papers", fake_fetch)
        await feed.feed_topic(db_session, topic)
        assert seen_since == [datetime(2026, 10, 1)]

    async def test_first_poll_looks_back_30_days(
        self, db_session: AsyncSession, monkeypatch
    ):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        now = datetime(2026, 10, 15, 12, 0, 0)
        seen_since: list[datetime] = []

        async def fake_fetch(t, *, since):
            seen_since.append(since)
            return []

        monkeypatch.setattr(feed, "fetch_feed_papers", fake_fetch)
        await feed.feed_topic(db_session, topic, now=now)
        assert seen_since == [now - timedelta(days=feed.FEED_LOOKBACK_DAYS)]


class TestFetchPerKind:
    def test_subject_hits_exa_search_with_date(self, monkeypatch):
        calls: dict = {}

        class FakeExa:
            def _fetch_papers_from_exa(self, query, k, **kwargs):
                calls.update(query=query, k=k, **kwargs)
                return [_paper("1"), _paper("2")]

        monkeypatch.setattr(feed, "_exa_client", lambda: FakeExa())
        since = datetime(2026, 10, 1)
        papers = feed._fetch_candidates_sync(
            "subject", "implicit neural representations", since=since
        )  # noqa: SLF001
        assert calls["query"] == "implicit neural representations"
        assert calls["category"] == "publication"
        assert calls["start_published_date"] == since
        assert len(papers) == 2

    def test_paper_kind_uses_find_similar(self, monkeypatch):
        calls: dict = {}

        class FakeExa:
            def find_similar_papers(self, url, **kwargs):
                calls.update(url=url, **kwargs)
                return [_paper("3")]

        monkeypatch.setattr(feed, "_exa_client", lambda: FakeExa())
        since = datetime(2026, 10, 1)
        papers = feed._fetch_candidates_sync(
            "paper", "https://arxiv.org/abs/2401.0001", since=since
        )  # noqa: SLF001
        assert calls["url"] == "https://arxiv.org/abs/2401.0001"
        assert calls["start_published_date"] == since
        assert [p.id for p in papers] == ["3"]

    def test_author_kind_maps_and_filters_by_year(self, monkeypatch):
        from services.paper.metadata.author_search import AuthorPaper, AuthorResult

        monkeypatch.setattr(
            "services.paper.metadata.author_search.search_authors",
            lambda name, **kw: [
                AuthorResult(
                    author_id="123",
                    name="Yann LeCun",
                    aliases=[],
                    url="",
                    paper_count=1,
                    citation_count=1,
                    h_index=1,
                )
            ],
        )
        monkeypatch.setattr(
            "services.paper.metadata.author_search.get_author_papers",
            lambda aid, **kw: [
                AuthorPaper(
                    paper_id="s2a",
                    title="Old",
                    year=2020,
                    citation_count=0,
                    venue=None,
                    url=None,
                    abstract=None,
                    external_ids={"ArXiv": "2001.0001"},
                ),
                AuthorPaper(
                    paper_id="s2b",
                    title="New",
                    year=2026,
                    citation_count=0,
                    venue=None,
                    url=None,
                    abstract=None,
                    external_ids={"ArXiv": "2601.0002"},
                ),
            ],
        )
        monkeypatch.setattr(
            "services.paper.metadata.author_search.get_paper_url",
            lambda ap: (
                f"https://arxiv.org/abs/{ap.external_ids['ArXiv']}"
                if ap.external_ids.get("ArXiv")
                else None
            ),
        )
        papers = feed._fetch_candidates_sync(  # noqa: SLF001
            "author", "papers by Yann LeCun", since=datetime(2025, 1, 1)
        )
        assert [p.title for p in papers] == ["New"]
        assert str(papers[0].url) == "https://arxiv.org/abs/2601.0002"
        assert papers[0].published_date == "2026"

    def test_author_kind_unavailable_returns_empty(self, monkeypatch):
        from services.paper.metadata.author_search import S2UnavailableError

        def _raise(name, **kw):
            raise S2UnavailableError("429")

        monkeypatch.setattr(
            "services.paper.metadata.author_search.search_authors", _raise
        )
        assert (
            feed._fetch_candidates_sync(
                "author", "papers by X", since=datetime(2025, 1, 1)
            )
            == []
        )  # noqa: SLF001


class TestTombstones:
    async def test_feed_never_resurrects_removed_paper(
        self, db_session: AsyncSession, monkeypatch
    ):
        from services.persistence.repositories import (
            list_topic_papers,
            remove_topic_paper,
        )

        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await add_topic_papers(
            db_session, topic_id=topic.id, papers=[_paper("2501.0001")], source="feed"
        )
        await db_session.commit()
        assert await remove_topic_paper(
            db_session, topic_id=topic.id, paper_id="2501.0001"
        )

        async def fake_fetch(t, *, since):
            return [_paper("2501.0001")]  # provider still returns it

        monkeypatch.setattr(feed, "fetch_feed_papers", fake_fetch)
        added = await feed.feed_topic(db_session, topic)
        assert added == 0
        assert await list_topic_papers(db_session, topic_id=topic.id) == []

    async def test_explicit_add_clears_tombstone(self, db_session: AsyncSession):
        from services.persistence.repositories import (
            list_topic_papers,
            remove_topic_paper,
        )

        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await add_topic_papers(
            db_session, topic_id=topic.id, papers=[_paper("2501.0001")]
        )
        await db_session.commit()
        await remove_topic_paper(db_session, topic_id=topic.id, paper_id="2501.0001")
        # A non-feed add (seed/manual) resurrects — the user put it back.
        added = await add_topic_papers(
            db_session, topic_id=topic.id, papers=[_paper("2501.0001")], source="seed"
        )
        await db_session.commit()
        assert added == 1
        papers = await list_topic_papers(db_session, topic_id=topic.id)
        assert [p.paper_id for p in papers] == ["2501.0001"]
