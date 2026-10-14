"""Unit tests for the anonymous→authenticated ownership claim.

``claim_anonymous_session`` runs on OAuth sign-in so a visitor's sess-owned
topics/reports/library/prefs land on their account instead of being orphaned.
Uses the same in-memory SQLite pattern as test_repositories.py.
"""

from __future__ import annotations

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

from services.persistence.database import Base  # noqa: E402
from services.persistence.models import (  # noqa: E402
    ChatMessage,
    ChatThread,
    Report,
    SavedPaper,
    Topic,
    TopicPaper,
    UsageEvent,
    UserPreference,
)
from services.persistence.repositories import (  # noqa: E402
    append_chat_message,
    claim_anonymous_session,
    create_report,
    upsert_google_user,
)

ANON = "sess:anon-123"


@pytest.fixture(autouse=True)
def _mock_crypto(monkeypatch):
    monkeypatch.setattr(
        "services.persistence.repositories.encrypt_secret", lambda v: f"enc:{v}"
    )
    monkeypatch.setattr(
        "services.persistence.repositories.decrypt_secret",
        lambda v: v.removeprefix("enc:"),
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


async def _make_user(session: AsyncSession, *, email: str = "u@example.com") -> str:
    user = await upsert_google_user(
        session,
        provider_user_id=f"g-{email}",
        email=email,
        name="U",
        avatar_url=None,
    )
    return user.id


def _make_topic(owner_id: str, key: str = "subject:inr", **kw) -> Topic:
    return Topic(
        owner_id=owner_id,
        kind="subject",
        canonical_key=key,
        title=kw.pop("title", "INR"),
        seed_query=kw.pop("seed_query", "implicit neural representations"),
        **kw,
    )


class TestPlainClaim:
    async def test_claims_all_owner_keyed_tables(self, db_session: AsyncSession):
        uid = await _make_user(db_session)

        report = await create_report(
            db_session,
            user_id=ANON,
            title="Anon report",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="m",
            is_url_input=False,
            report_html="<p>x</p>",
            papers=[],
        )
        msg = await append_chat_message(
            db_session,
            user_id=ANON,
            report_id=report.id,
            paper_id="p1",
            role="user",
            content="hi",
        )
        db_session.add(SavedPaper(owner_id=ANON, paper_id="p9", title="t"))
        db_session.add(UsageEvent(user_id=ANON, event_type="search"))
        topic = _make_topic(ANON)
        db_session.add(topic)
        await db_session.commit()

        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )

        assert stats["topics"] == 1
        assert stats["chat_threads"] == 1
        assert stats["saved_papers"] == 1

        assert (await db_session.get(Report, report.id)).user_id == uid
        assert (await db_session.get(Topic, topic.id)).owner_id == uid
        thread = await db_session.get(ChatThread, msg.thread_id)
        assert thread.user_id == uid
        saved = await db_session.scalar(
            select(SavedPaper).where(SavedPaper.paper_id == "p9")
        )
        assert saved.owner_id == uid
        evt = await db_session.scalar(select(UsageEvent))
        assert evt.user_id == uid

    async def test_no_anon_rows_is_noop(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )
        assert stats == {}

    async def test_idempotent(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        await create_report(
            db_session,
            user_id=ANON,
            title="r",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="m",
            is_url_input=False,
            report_html="<p>x</p>",
            papers=[],
        )
        for _ in range(2):
            await claim_anonymous_session(
                db_session, user_id=uid, session_id="anon-123"
            )
        reports = (await db_session.execute(select(Report))).scalars().all()
        assert len(reports) == 1
        assert reports[0].user_id == uid


class TestTopicMerge:
    async def test_same_key_merges_into_user_topic(self, db_session: AsyncSession):
        uid = await _make_user(db_session)

        user_topic = _make_topic(uid, title="User INR")
        db_session.add(user_topic)
        await db_session.flush()
        db_session.add(TopicPaper(topic_id=user_topic.id, paper_id="p-a", title="A"))

        sess_topic = _make_topic(ANON, blog_markdown="# blog", notes_markdown="notes")
        db_session.add(sess_topic)
        await db_session.flush()
        db_session.add(TopicPaper(topic_id=sess_topic.id, paper_id="p-b", title="B"))
        db_session.add(
            TopicPaper(topic_id=sess_topic.id, paper_id="p-a", title="A dup")
        )
        report = await create_report(
            db_session,
            user_id=ANON,
            title="r",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="m",
            is_url_input=False,
            report_html="<p>x</p>",
            papers=[],
            topic_id=sess_topic.id,
        )
        await append_chat_message(
            db_session,
            user_id=ANON,
            report_id=None,
            paper_id="topic",
            role="user",
            content="hi",
            topic_id=sess_topic.id,
        )
        await db_session.commit()

        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )

        assert stats["topics_merged"] == 1
        assert "topics" not in stats

        # sess topic deleted; user topic gained p-b but not the p-a dupe
        assert await db_session.get(Topic, sess_topic.id) is None
        papers = (
            (
                await db_session.execute(
                    select(TopicPaper.paper_id).where(
                        TopicPaper.topic_id == user_topic.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(papers) == ["p-a", "p-b"]

        # content carried over (user's was empty)
        t = await db_session.get(Topic, user_topic.id)
        assert t.blog_markdown == "# blog"
        assert t.notes_markdown == "notes"

        # report + thread repointed to the surviving topic and re-owned
        assert (await db_session.get(Report, report.id)).topic_id == user_topic.id
        assert (await db_session.get(Report, report.id)).user_id == uid
        thread = await db_session.scalar(select(ChatThread))
        assert thread.topic_id == user_topic.id
        assert thread.user_id == uid

    async def test_user_topic_content_wins(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        user_topic = _make_topic(uid, blog_markdown="# mine")
        db_session.add(user_topic)
        sess_topic = _make_topic(ANON, blog_markdown="# anon")
        db_session.add(sess_topic)
        await db_session.commit()

        await claim_anonymous_session(db_session, user_id=uid, session_id="anon-123")
        assert (await db_session.get(Topic, user_topic.id)).blog_markdown == "# mine"


class TestThreadMerge:
    async def test_messages_move_into_user_thread(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        user_msg = await append_chat_message(
            db_session,
            user_id=uid,
            report_id="r1",
            paper_id="p1",
            role="user",
            content="user msg",
        )
        sess_msg = await append_chat_message(
            db_session,
            user_id=ANON,
            report_id="r1",
            paper_id="p1",
            role="user",
            content="anon msg",
        )
        assert user_msg.thread_id != sess_msg.thread_id

        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )
        assert stats["chat_threads_merged"] == 1

        msgs = (
            (
                await db_session.execute(
                    select(ChatMessage).where(
                        ChatMessage.thread_id == user_msg.thread_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {m.content for m in msgs} == {"user msg", "anon msg"}
        remaining = (await db_session.execute(select(ChatThread))).scalars().all()
        assert len(remaining) == 1


class TestLibraryDedupe:
    async def test_keeps_user_row_takes_advanced_status(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        db_session.add(
            SavedPaper(
                owner_id=uid,
                paper_id="p1",
                title="mine",
                status="to_read",
            )
        )
        db_session.add(
            SavedPaper(
                owner_id=ANON,
                paper_id="p1",
                title="anon",
                status="done",
                notion_page_id="np-1",
            )
        )
        db_session.add(SavedPaper(owner_id=ANON, paper_id="p2", title="new"))
        await db_session.commit()

        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )
        assert stats["saved_papers"] == 1
        assert stats["saved_papers_deduped"] == 1

        rows = (await db_session.execute(select(SavedPaper))).scalars().all()
        assert len(rows) == 2
        p1 = next(r for r in rows if r.paper_id == "p1")
        # account's row survives but inherits the more advanced status + notion link
        assert p1.title == "mine"
        assert p1.status == "done"
        assert p1.notion_page_id == "np-1"


class TestPrefsClaim:
    async def test_reowns_pref_row_when_user_has_none(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        db_session.add(
            UserPreference(
                owner_id=ANON,
                model_pins={"fast": "m/a"},
                openrouter_key_encrypted="enc:k",
            )
        )
        await db_session.commit()

        stats = await claim_anonymous_session(
            db_session, user_id=uid, session_id="anon-123"
        )
        assert stats["preferences"] == 1

        pref = await db_session.get(UserPreference, uid)
        assert pref is not None
        assert pref.model_pins == {"fast": "m/a"}
        assert pref.openrouter_key_encrypted == "enc:k"
        assert await db_session.get(UserPreference, ANON) is None

    async def test_merges_into_existing_pref_user_wins(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        db_session.add(
            UserPreference(
                owner_id=uid,
                model_pins={"deep": "m/user"},
            )
        )
        db_session.add(
            UserPreference(
                owner_id=ANON,
                model_pins={"fast": "m/anon", "deep": "m/anon"},
                openrouter_key_encrypted="enc:anon",
            )
        )
        await db_session.commit()

        await claim_anonymous_session(db_session, user_id=uid, session_id="anon-123")

        pref = await db_session.get(UserPreference, uid)
        # anon's extra tier merged in; the shared tier stays the user's
        assert pref.model_pins == {"fast": "m/anon", "deep": "m/user"}
        # anonymous BYOK carried since the user had none
        assert pref.openrouter_key_encrypted == "enc:anon"
        assert await db_session.get(UserPreference, ANON) is None
