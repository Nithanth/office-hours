"""Facet repo tests: seed backfill, add/remove, merge, never-last-facet."""

from __future__ import annotations

import pytest

aiosqlite = pytest.importorskip(
    "aiosqlite", reason="aiosqlite is required for SQLite async tests"
)

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from services.persistence.database import Base  # noqa: E402
from services.persistence.models import Topic, TopicPaper  # noqa: E402
from services.persistence.repositories import (  # noqa: E402
    add_topic_facet,
    ensure_seed_facet,
    list_topic_facets,
    merge_topics,
    remove_topic_facet,
)


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _topic(owner="sess:o", **kw) -> Topic:
    return Topic(
        owner_id=owner,
        kind=kw.pop("kind", "subject"),
        canonical_key=kw.pop("canonical_key", "subject:inr"),
        title=kw.pop("title", "INR"),
        seed_query=kw.pop("seed_query", "implicit neural representations"),
        **kw,
    )


class TestSeedFacet:
    async def test_ensure_seed_creates_one(self, db_session: AsyncSession):
        t = _topic()
        db_session.add(t)
        await db_session.commit()
        await ensure_seed_facet(db_session, topic=t)
        facets = await list_topic_facets(db_session, topic_id=t.id)
        assert len(facets) == 1
        assert facets[0].query == "implicit neural representations"
        # idempotent — never duplicates
        await ensure_seed_facet(db_session, topic=t)
        assert len(await list_topic_facets(db_session, topic_id=t.id)) == 1


class TestFacetOps:
    async def test_add_and_dedupe(self, db_session: AsyncSession):
        t = _topic()
        db_session.add(t)
        await db_session.commit()
        f1, created1 = await add_topic_facet(
            db_session,
            topic_id=t.id,
            kind="subject",
            query="NeRF",
            canonical_key="subject:nerf",
        )
        f2, created2 = await add_topic_facet(
            db_session,
            topic_id=t.id,
            kind="subject",
            query="NeRF",
            canonical_key="subject:nerf",
        )
        assert created1 and not created2 and f1.id == f2.id

    async def test_last_facet_never_removed(self, db_session: AsyncSession):
        t = _topic()
        db_session.add(t)
        await db_session.commit()
        f, _ = await add_topic_facet(
            db_session,
            topic_id=t.id,
            kind="subject",
            query="q",
            canonical_key="subject:q",
        )
        assert not await remove_topic_facet(db_session, facet_id=f.id, topic_id=t.id)
        f2, _ = await add_topic_facet(
            db_session,
            topic_id=t.id,
            kind="subject",
            query="q2",
            canonical_key="subject:q2",
        )
        assert await remove_topic_facet(db_session, facet_id=f2.id, topic_id=t.id)


class TestMerge:
    async def test_merge_moves_facets_papers_archives(self, db_session: AsyncSession):
        a = _topic(title="INR", canonical_key="subject:inr")
        b = _topic(title="NeRF", canonical_key="subject:nerf")
        db_session.add_all([a, b])
        await db_session.commit()
        await add_topic_facet(
            db_session,
            topic_id=a.id,
            kind="subject",
            query="inr",
            canonical_key="subject:inr",
        )
        await add_topic_facet(
            db_session,
            topic_id=b.id,
            kind="subject",
            query="nerf",
            canonical_key="subject:nerf",
        )
        db_session.add(
            TopicPaper(
                topic_id=b.id,
                paper_id="p1",
                title="NeRF paper",
                url="u",
                authors={},
                source="seed",
            )
        )
        await db_session.commit()

        target = await merge_topics(
            db_session, source_id=b.id, into_id=a.id, owner_id="sess:o"
        )
        assert target is not None and target.id == a.id
        facets = await list_topic_facets(db_session, topic_id=a.id)
        assert {f.canonical_key for f in facets} == {"subject:inr", "subject:nerf"}
        papers = (
            (
                await db_session.execute(
                    __import__("sqlalchemy")
                    .select(TopicPaper)
                    .where(TopicPaper.topic_id == a.id)
                )
            )
            .scalars()
            .all()
        )
        assert [p.paper_id for p in papers] == ["p1"]
        await db_session.refresh(b)
        assert b.archived
