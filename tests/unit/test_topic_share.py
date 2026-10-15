"""Shareable topics — token lifecycle + public read-only page."""

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

from models.paper import Paper  # noqa: E402
from services.persistence.models import Base, Topic  # noqa: E402
from services.persistence.repositories import (  # noqa: E402
    add_topic_papers,
    get_topic_by_share_token,
    list_topic_papers,
    set_topic_share_token,
)


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
        kind="subject",
        canonical_key="subject:inr",
        title="INR",
        seed_query="implicit neural representations",
        **kw,
    )


class TestShareToken:
    async def test_lookup_by_token(self, db_session: AsyncSession):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await set_topic_share_token(
            db_session, owner_id="sess:o", topic_id=topic.id, token="tok123"
        )
        found = await get_topic_by_share_token(db_session, token="tok123")
        assert found is not None and found.id == topic.id

    async def test_wrong_owner_cannot_set(self, db_session: AsyncSession):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await set_topic_share_token(
            db_session, owner_id="sess:other", topic_id=topic.id, token="x"
        )
        assert topic.share_token is None

    async def test_unshare_disables_lookup(self, db_session: AsyncSession):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await set_topic_share_token(
            db_session, owner_id="sess:o", topic_id=topic.id, token="tok123"
        )
        await set_topic_share_token(
            db_session, owner_id="sess:o", topic_id=topic.id, token=None
        )
        assert await get_topic_by_share_token(db_session, token="tok123") is None

    async def test_shared_topic_still_lists_papers(self, db_session: AsyncSession):
        topic = _topic()
        db_session.add(topic)
        await db_session.commit()
        await add_topic_papers(
            db_session,
            topic_id=topic.id,
            papers=[Paper(id="p1", title="P1", url="https://arxiv.org/abs/p1")],
        )
        await db_session.commit()
        await set_topic_share_token(
            db_session, owner_id="sess:o", topic_id=topic.id, token="tok123"
        )
        found = await get_topic_by_share_token(db_session, token="tok123")
        papers = await list_topic_papers(db_session, topic_id=found.id)
        assert [p.paper_id for p in papers] == ["p1"]
