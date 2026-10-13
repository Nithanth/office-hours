"""Unit tests for the persistence-layer repository helpers.

Uses an in-memory SQLite database so the tests are fast, isolated,
and do not require any external services.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

aiosqlite = pytest.importorskip(
    "aiosqlite", reason="aiosqlite is required for SQLite async tests"
)

from sqlalchemy.ext.asyncio import (  # noqa: E402 -- after optional aiosqlite guard
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from models.paper import Paper  # noqa: E402 -- after optional aiosqlite guard
from services.persistence.database import (  # noqa: E402 -- after optional aiosqlite guard
    Base,
)
from services.persistence.models import (  # noqa: E402 -- after optional aiosqlite guard
    ChatMessage,
    ChatThread,
    Export,
    OAuthAccount,
    UsageEvent,
)
from services.persistence.repositories import (  # noqa: E402 -- after optional aiosqlite guard
    append_chat_message,
    create_report,
    daily_usage_count,
    get_report,
    get_user,
    html_to_text,
    list_reports,
    record_export,
    record_usage,
    upsert_google_user,
    usage_count_since,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_crypto(monkeypatch):
    """Replace encrypt/decrypt with trivial stubs so we don't need a real key."""
    monkeypatch.setattr(
        "services.persistence.repositories.encrypt_secret", lambda v: f"enc:{v}"
    )
    monkeypatch.setattr(
        "services.persistence.repositories.decrypt_secret",
        lambda v: v.removeprefix("enc:"),
    )


@pytest.fixture
async def db_session():
    """Yield an async session backed by an in-memory SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _make_user(session: AsyncSession, *, email: str = "alice@example.com") -> str:
    """Helper: insert a Google user and return their user id."""
    user = await upsert_google_user(
        session,
        provider_user_id=f"goog-{email}",
        email=email,
        name="Alice",
        avatar_url="https://img.example.com/alice.png",
        access_token="tok-access",
        refresh_token="tok-refresh",
    )
    return user.id


# ---------------------------------------------------------------------------
# html_to_text (pure function – no DB needed)
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_strips_tags(self):
        assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_removes_script_and_style(self):
        html = (
            "<style>body{color:red}</style>"
            "<script>alert(1)</script>"
            "<p>Content</p>"
        )
        assert html_to_text(html) == "Content"

    def test_normalises_whitespace(self):
        assert html_to_text("<div>  lots   of   space  </div>") == "lots of space"

    def test_empty_string(self):
        assert html_to_text("") == ""


# ---------------------------------------------------------------------------
# upsert_google_user / get_user
# ---------------------------------------------------------------------------


class TestUpsertGoogleUser:
    async def test_creates_user_and_oauth_account(self, db_session: AsyncSession):
        user = await upsert_google_user(
            db_session,
            provider_user_id="g-123",
            email="bob@example.com",
            name="Bob",
            avatar_url=None,
            access_token="at",
            refresh_token="rt",
        )
        assert user.id is not None
        assert user.email == "bob@example.com"
        assert user.name == "Bob"

        # OAuth row created
        from sqlalchemy import select

        row = await db_session.scalar(
            select(OAuthAccount).where(OAuthAccount.provider_user_id == "g-123")
        )
        assert row is not None
        assert row.user_id == user.id
        assert row.access_token_encrypted == "enc:at"
        assert row.refresh_token_encrypted == "enc:rt"

    async def test_second_call_updates_existing_user(self, db_session: AsyncSession):
        user1 = await upsert_google_user(
            db_session,
            provider_user_id="g-456",
            email="carol@example.com",
            name="Carol",
            avatar_url=None,
            access_token="tok1",
        )
        user2 = await upsert_google_user(
            db_session,
            provider_user_id="g-456",
            email="carol@example.com",
            name="Carol Updated",
            avatar_url="https://img.example.com/carol.png",
            access_token="tok2",
        )
        assert user1.id == user2.id
        assert user2.name == "Carol Updated"
        assert user2.avatar_url == "https://img.example.com/carol.png"


class TestGetUser:
    async def test_returns_user(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        user = await get_user(db_session, uid)
        assert user is not None
        assert user.email == "alice@example.com"

    async def test_returns_none_for_unknown(self, db_session: AsyncSession):
        assert await get_user(db_session, "nonexistent-id") is None


# ---------------------------------------------------------------------------
# record_usage / usage_count_since / daily_usage_count
# ---------------------------------------------------------------------------


class TestUsageTracking:
    async def test_record_and_count(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        await record_usage(db_session, user_id=uid, event_type="search", provider="exa")
        await record_usage(db_session, user_id=uid, event_type="search", provider="exa")
        await record_usage(db_session, user_id=uid, event_type="export", provider=None)

        count = await usage_count_since(
            db_session,
            user_id=uid,
            event_type="search",
            since=datetime.utcnow() - timedelta(hours=1),
        )
        assert count == 2

    async def test_daily_usage_count(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        await record_usage(db_session, user_id=uid, event_type="chat")
        count = await daily_usage_count(db_session, user_id=uid, event_type="chat")
        assert count == 1

    async def test_count_zero_when_no_events(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        count = await daily_usage_count(db_session, user_id=uid, event_type="search")
        assert count == 0


# ---------------------------------------------------------------------------
# create_report / list_reports / get_report
# ---------------------------------------------------------------------------


def _sample_papers() -> list[Paper]:
    return [
        Paper(
            id="p1",
            title="Test Paper One",
            url="https://example.com/p1",
            authors=["Alice", "Bob"],
        ),
        Paper(
            id="p2",
            title="Test Paper Two",
            url="https://example.com/p2",
            authors=["Carol"],
        ),
    ]


class TestReports:
    async def test_create_report_stores_papers_and_usage_event(
        self, db_session: AsyncSession
    ):
        uid = await _make_user(db_session)
        report = await create_report(
            db_session,
            user_id=uid,
            title="My Report",
            original_query="quantum computing",
            audience_type="expert",
            summary_size="long",
            model_key="openai/gpt-4o",
            is_url_input=False,
            report_html="<h1>Results</h1><p>Findings here.</p>",
            papers=_sample_papers(),
        )
        assert report.id is not None
        assert report.title == "My Report"
        assert report.report_text == "Results Findings here."

        # Papers persisted
        from sqlalchemy import select

        from services.persistence.models import ReportPaper

        papers = (
            (
                await db_session.execute(
                    select(ReportPaper).where(ReportPaper.report_id == report.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(papers) == 2
        titles = {p.title for p in papers}
        assert titles == {"Test Paper One", "Test Paper Two"}

        # Usage event recorded
        evt = await db_session.scalar(
            select(UsageEvent).where(
                UsageEvent.user_id == uid,
                UsageEvent.event_type == "report_generated",
            )
        )
        assert evt is not None
        assert evt.metadata_json["paper_count"] == 2

    async def test_list_reports_returns_user_reports_only(
        self, db_session: AsyncSession
    ):
        uid_a = await _make_user(db_session, email="a@example.com")
        uid_b = await _make_user(db_session, email="b@example.com")

        await create_report(
            db_session,
            user_id=uid_a,
            title="Report A",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="openai/gpt-4o",
            is_url_input=False,
            report_html="<p>A</p>",
            papers=[],
        )
        await create_report(
            db_session,
            user_id=uid_b,
            title="Report B",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="openai/gpt-4o",
            is_url_input=False,
            report_html="<p>B</p>",
            papers=[],
        )

        reports_a = await list_reports(db_session, user_id=uid_a)
        assert len(reports_a) == 1
        assert reports_a[0].title == "Report A"

    async def test_get_report_scoped_by_user(self, db_session: AsyncSession):
        uid_a = await _make_user(db_session, email="owner@example.com")
        uid_b = await _make_user(db_session, email="other@example.com")

        report = await create_report(
            db_session,
            user_id=uid_a,
            title="Owner's Report",
            original_query="q",
            audience_type="general",
            summary_size="short",
            model_key="openai/gpt-4o",
            is_url_input=False,
            report_html="<p>content</p>",
            papers=[],
        )

        # Owner can see it
        assert (
            await get_report(db_session, user_id=uid_a, report_id=report.id) is not None
        )
        # Other user cannot
        assert await get_report(db_session, user_id=uid_b, report_id=report.id) is None


# ---------------------------------------------------------------------------
# append_chat_message
# ---------------------------------------------------------------------------


class TestChatMessages:
    async def test_creates_thread_on_first_message(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        msg = await append_chat_message(
            db_session,
            user_id=uid,
            report_id=None,
            paper_id="paper-abc",
            role="user",
            content="Hello!",
            title="Chat about paper-abc",
        )
        assert msg.id is not None
        assert msg.role == "user"
        assert msg.content == "Hello!"

        # A thread was created
        from sqlalchemy import select

        thread = await db_session.scalar(
            select(ChatThread).where(ChatThread.id == msg.thread_id)
        )
        assert thread is not None
        assert thread.paper_id == "paper-abc"
        assert thread.title == "Chat about paper-abc"

    async def test_appends_to_existing_thread(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        msg1 = await append_chat_message(
            db_session,
            user_id=uid,
            report_id=None,
            paper_id="paper-xyz",
            role="user",
            content="First message",
        )
        msg2 = await append_chat_message(
            db_session,
            user_id=uid,
            report_id=None,
            paper_id="paper-xyz",
            role="assistant",
            content="Second message",
        )
        # Both messages belong to the same thread
        assert msg1.thread_id == msg2.thread_id

        # Verify two messages in the thread
        from sqlalchemy import select

        msgs = (
            (
                await db_session.execute(
                    select(ChatMessage).where(ChatMessage.thread_id == msg1.thread_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(msgs) == 2


# ---------------------------------------------------------------------------
# record_export
# ---------------------------------------------------------------------------


class TestRecordExport:
    async def test_records_export_event(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        await record_export(
            db_session,
            user_id=uid,
            report_id=None,
            export_type="pdf",
            destination="local",
            url="https://cdn.example.com/report.pdf",
            status="success",
        )

        from sqlalchemy import select

        export = await db_session.scalar(select(Export).where(Export.user_id == uid))
        assert export is not None
        assert export.export_type == "pdf"
        assert export.destination == "local"
        assert export.url == "https://cdn.example.com/report.pdf"
        assert export.status == "success"
        assert export.error is None

    async def test_records_failed_export(self, db_session: AsyncSession):
        uid = await _make_user(db_session)
        await record_export(
            db_session,
            user_id=uid,
            report_id=None,
            export_type="notion",
            destination="workspace-123",
            url=None,
            status="error",
            error="Notion API rate limit",
        )

        from sqlalchemy import select

        export = await db_session.scalar(select(Export).where(Export.user_id == uid))
        assert export is not None
        assert export.status == "error"
        assert export.error == "Notion API rate limit"


# ---------------------------------------------------------------------------
# Saved papers (library)
# ---------------------------------------------------------------------------


class TestSavedPapers:
    async def test_save_and_get(self, db_session: AsyncSession):
        from services.persistence.repositories import get_saved_paper, save_paper

        saved = await save_paper(
            db_session,
            owner_id="u1",
            paper_id="https://arxiv.org/abs/1706.03762",
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            authors={"list": ["Vaswani", "Shazeer"]},
        )
        assert saved.status == "to_read"

        fetched = await get_saved_paper(
            db_session, owner_id="u1", paper_id="https://arxiv.org/abs/1706.03762"
        )
        assert fetched is not None
        assert fetched.title == "Attention Is All You Need"

    async def test_resave_updates_metadata_keeps_status(self, db_session: AsyncSession):
        from services.persistence.repositories import (
            get_saved_paper,
            save_paper,
            set_saved_paper_status,
        )

        pid = "https://x.org/p1"
        await save_paper(db_session, owner_id="u1", paper_id=pid, title="t")
        await set_saved_paper_status(
            db_session, owner_id="u1", paper_id=pid, status="reading"
        )
        await save_paper(db_session, owner_id="u1", paper_id=pid, title="new title")
        saved = await get_saved_paper(db_session, owner_id="u1", paper_id=pid)
        assert saved is not None
        assert saved.title == "new title"
        assert saved.status == "reading"  # status not reset by re-save

    async def test_owner_isolation(self, db_session: AsyncSession):
        from services.persistence.repositories import list_saved_papers, save_paper

        await save_paper(db_session, owner_id="u1", paper_id="p1", title="t1")
        await save_paper(db_session, owner_id="u2", paper_id="p2", title="t2")
        mine = await list_saved_papers(db_session, owner_id="u1")
        assert [p.paper_id for p in mine] == ["p1"]

    async def test_status_filter_and_validation(self, db_session: AsyncSession):
        from services.persistence.repositories import (
            list_saved_papers,
            save_paper,
            set_saved_paper_status,
        )

        await save_paper(db_session, owner_id="u1", paper_id="p1", title="t1")
        await save_paper(db_session, owner_id="u1", paper_id="p2", title="t2")
        await set_saved_paper_status(
            db_session, owner_id="u1", paper_id="p1", status="done"
        )
        assert (
            await set_saved_paper_status(
                db_session, owner_id="u1", paper_id="p1", status="bogus"
            )
            is None
        )
        done = await list_saved_papers(db_session, owner_id="u1", status="done")
        assert [p.paper_id for p in done] == ["p1"]

    async def test_unsave(self, db_session: AsyncSession):
        from services.persistence.repositories import (
            get_saved_paper,
            save_paper,
            unsave_paper,
        )

        await save_paper(db_session, owner_id="u1", paper_id="p1", title="t")
        assert await unsave_paper(db_session, owner_id="u1", paper_id="p1")
        assert not await unsave_paper(db_session, owner_id="u1", paper_id="p1")
        assert await get_saved_paper(db_session, owner_id="u1", paper_id="p1") is None

    async def test_notion_page_id_roundtrip(self, db_session: AsyncSession):
        from services.persistence.repositories import (
            get_saved_paper,
            save_paper,
            set_saved_paper_notion_page,
        )

        await save_paper(db_session, owner_id="u1", paper_id="p1", title="t")
        await set_saved_paper_notion_page(
            db_session, owner_id="u1", paper_id="p1", notion_page_id="abc-123"
        )
        saved = await get_saved_paper(db_session, owner_id="u1", paper_id="p1")
        assert saved is not None
        assert saved.notion_page_id == "abc-123"
