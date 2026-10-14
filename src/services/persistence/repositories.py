"""Repository helpers for the private-beta persistence layer."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.paper import Paper
from services.auth.crypto import decrypt_secret, encrypt_secret
from services.persistence.models import (
    ChatMessage,
    ChatThread,
    Export,
    OAuthAccount,
    Report,
    ReportPaper,
    SavedPaper,
    UsageEvent,
    User,
    UserIntegration,
    UserPreference,
)


def html_to_text(value: str) -> str:
    clean = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", "", value, flags=re.I
    )
    clean = re.sub(
        r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", "", clean, flags=re.I
    )
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


async def upsert_google_user(
    session: AsyncSession,
    *,
    provider_user_id: str,
    email: str,
    name: str | None,
    avatar_url: str | None,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> User:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is None:
        existing = User(email=email, name=name, avatar_url=avatar_url)
        session.add(existing)
        await session.flush()
    else:
        existing.name = name or existing.name
        existing.avatar_url = avatar_url or existing.avatar_url
        existing.last_login_at = datetime.utcnow()

    account = await session.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == "google",
            OAuthAccount.provider_user_id == provider_user_id,
        )
    )
    encrypted_access = encrypt_secret(access_token) if access_token else None
    encrypted_refresh = encrypt_secret(refresh_token) if refresh_token else None
    if account is None:
        session.add(
            OAuthAccount(
                user_id=existing.id,
                provider="google",
                provider_user_id=provider_user_id,
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=encrypted_refresh,
            )
        )
    else:
        account.user_id = existing.id
        account.access_token_encrypted = (
            encrypted_access or account.access_token_encrypted
        )
        account.refresh_token_encrypted = (
            encrypted_refresh or account.refresh_token_encrypted
        )
        account.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(existing)
    return existing


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def record_usage(
    session: AsyncSession,
    *,
    user_id: str | None,
    event_type: str,
    provider: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        UsageEvent(
            user_id=user_id,
            event_type=event_type,
            provider=provider,
            metadata_json=metadata or {},
        )
    )
    await session.commit()


async def usage_count_since(
    session: AsyncSession,
    *,
    user_id: str,
    event_type: str,
    since: datetime,
) -> int:
    count = await session.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type == event_type,
            UsageEvent.created_at >= since,
        )
    )
    return int(count or 0)


async def daily_usage_count(
    session: AsyncSession, *, user_id: str, event_type: str
) -> int:
    return await usage_count_since(
        session,
        user_id=user_id,
        event_type=event_type,
        since=datetime.utcnow() - timedelta(days=1),
    )


async def create_report(
    session: AsyncSession,
    *,
    user_id: str,
    title: str,
    original_query: str,
    audience_type: str,
    summary_size: str,
    model_key: str,
    is_url_input: bool,
    report_html: str,
    papers: list[Paper],
    topic_id: str | None = None,
) -> Report:
    report = Report(
        user_id=user_id,
        topic_id=topic_id,
        title=title[:300] or "Research Report",
        original_query=original_query,
        audience_type=audience_type,
        summary_size=summary_size,
        model_key=model_key,
        is_url_input=is_url_input,
        report_html=report_html,
        report_text=html_to_text(report_html),
    )
    session.add(report)
    await session.flush()
    for paper in papers:
        session.add(
            ReportPaper(
                report_id=report.id,
                paper_id=paper.id,
                title=paper.title or "Untitled paper",
                url=str(paper.url) if paper.url else None,
                authors={"items": paper.authors or []},
                published_date=(
                    str(paper.published_date) if paper.published_date else None
                ),
                metadata_json={
                    "score": paper.relevance_score,
                    "source": paper.source,
                    "text_chars": len(paper.text_content or ""),
                },
            )
        )
    session.add(
        UsageEvent(
            user_id=user_id,
            event_type="report_generated",
            provider=None,
            metadata_json={"model_key": model_key, "paper_count": len(papers)},
        )
    )
    await session.commit()
    await session.refresh(report)
    return report


async def list_reports(
    session: AsyncSession, *, user_id: str, limit: int = 50
) -> list[Report]:
    result = await session.execute(
        select(Report)
        .where(Report.user_id == user_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_report(
    session: AsyncSession, *, user_id: str, report_id: str
) -> Report | None:
    return await session.scalar(
        select(Report).where(Report.id == report_id, Report.user_id == user_id)
    )


async def set_report_blog(
    session: AsyncSession, *, user_id: str, report_id: str, markdown: str
) -> Report | None:
    """Persist generated blog markdown on the report row (no-op if missing)."""
    report = await get_report(session, user_id=user_id, report_id=report_id)
    if report is None:
        return None
    report.blog_markdown = markdown
    await session.commit()
    await session.refresh(report)
    return report


async def append_chat_message(
    session: AsyncSession,
    *,
    user_id: str,
    report_id: str | None,
    paper_id: str,
    role: str,
    content: str,
    title: str | None = None,
) -> ChatMessage:
    thread = await session.scalar(
        select(ChatThread).where(
            ChatThread.user_id == user_id,
            ChatThread.report_id == report_id,
            ChatThread.paper_id == paper_id,
        )
    )
    if thread is None:
        thread = ChatThread(
            user_id=user_id, report_id=report_id, paper_id=paper_id, title=title
        )
        session.add(thread)
        await session.flush()
    else:
        thread.updated_at = datetime.utcnow()
    message = ChatMessage(thread_id=thread.id, role=role, content=content)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def get_chat_thread_messages(
    session: AsyncSession,
    *,
    user_id: str,
    report_id: str | None,
    paper_id: str,
    limit: int = 200,
) -> list[ChatMessage]:
    """Persisted chat messages for a thread, oldest first."""
    thread = await session.scalar(
        select(ChatThread).where(
            ChatThread.user_id == user_id,
            ChatThread.report_id == report_id,
            ChatThread.paper_id == paper_id,
        )
    )
    if thread is None:
        return []
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def upsert_notion_integration(
    session: AsyncSession,
    *,
    user_id: str,
    access_token: str,
    refresh_token: str | None,
    bot_id: str | None,
    workspace_id: str | None,
    workspace_name: str | None,
    workspace_icon: str | None,
) -> UserIntegration:
    integration = await session.scalar(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == "notion",
        )
    )
    encrypted_access = encrypt_secret(access_token)
    encrypted_refresh = encrypt_secret(refresh_token) if refresh_token else None
    if integration is None:
        integration = UserIntegration(
            user_id=user_id,
            provider="notion",
            access_token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
            bot_id=bot_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            workspace_icon=workspace_icon,
        )
        session.add(integration)
    else:
        integration.access_token_encrypted = encrypted_access
        integration.refresh_token_encrypted = (
            encrypted_refresh or integration.refresh_token_encrypted
        )
        integration.bot_id = bot_id
        integration.workspace_id = workspace_id
        integration.workspace_name = workspace_name
        integration.workspace_icon = workspace_icon
        integration.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(integration)
    return integration


async def get_notion_integration(
    session: AsyncSession,
    *,
    user_id: str,
) -> UserIntegration | None:
    return await session.scalar(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == "notion",
        )
    )


def notion_access_token(integration: UserIntegration) -> str:
    return str(decrypt_secret(integration.access_token_encrypted))


async def update_notion_destination(
    session: AsyncSession,
    *,
    user_id: str,
    parent_page_id: str | None,
    parent_database_id: str | None,
) -> UserIntegration | None:
    integration = await get_notion_integration(session, user_id=user_id)
    if integration is None:
        return None
    integration.parent_page_id = parent_page_id
    integration.parent_database_id = parent_database_id
    integration.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(integration)
    return integration


async def set_notion_databases(
    session: AsyncSession,
    *,
    user_id: str,
    parent_database_id: str | None,
    topics_database_id: str | None,
) -> None:
    integration = await get_notion_integration(session, user_id=user_id)
    if integration is not None:
        if parent_database_id:
            integration.parent_database_id = parent_database_id
        integration.topics_database_id = topics_database_id
        integration.updated_at = datetime.utcnow()
        await session.commit()


async def set_report_notion_page(
    session: AsyncSession, *, user_id: str, report_id: str, notion_page_id: str
) -> None:
    report = await get_report(session, user_id=user_id, report_id=report_id)
    if report is not None:
        report.notion_page_id = notion_page_id
        await session.commit()


async def delete_notion_integration(session: AsyncSession, *, user_id: str) -> None:
    await session.execute(
        delete(UserIntegration).where(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == "notion",
        )
    )
    await session.commit()


async def record_export(
    session: AsyncSession,
    *,
    user_id: str,
    report_id: str | None,
    export_type: str,
    destination: str | None,
    url: str | None,
    status: str,
    error: str | None = None,
) -> None:
    session.add(
        Export(
            user_id=user_id,
            report_id=report_id,
            export_type=export_type,
            destination=destination,
            url=url,
            status=status,
            error=error,
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# User preferences (model pins, BYOK)
# ---------------------------------------------------------------------------


async def get_user_preference(
    session: AsyncSession, *, owner_id: str
) -> UserPreference | None:
    return await session.get(UserPreference, owner_id)


async def upsert_model_pins(
    session: AsyncSession, *, owner_id: str, pins: dict[str, str]
) -> UserPreference:
    pref = await session.get(UserPreference, owner_id)
    if pref is None:
        pref = UserPreference(owner_id=owner_id, model_pins=dict(pins))
        session.add(pref)
    else:
        # Reassign (not mutate) so SQLAlchemy tracks the JSON change.
        pref.model_pins = dict(pins)
    await session.commit()
    await session.refresh(pref)
    return pref


async def set_openrouter_key(
    session: AsyncSession, *, owner_id: str, api_key: str
) -> UserPreference:
    pref = await session.get(UserPreference, owner_id)
    encrypted = encrypt_secret(api_key)
    if pref is None:
        pref = UserPreference(owner_id=owner_id, openrouter_key_encrypted=encrypted)
        session.add(pref)
    else:
        pref.openrouter_key_encrypted = encrypted
    await session.commit()
    await session.refresh(pref)
    return pref


async def delete_openrouter_key(session: AsyncSession, *, owner_id: str) -> None:
    pref = await session.get(UserPreference, owner_id)
    if pref is not None:
        pref.openrouter_key_encrypted = None
        await session.commit()


def openrouter_api_key(pref: UserPreference) -> str | None:
    if not pref.openrouter_key_encrypted:
        return None
    return str(decrypt_secret(pref.openrouter_key_encrypted))


# ---------------------------------------------------------------------------
# Saved papers (persistent library)
# ---------------------------------------------------------------------------


async def save_paper(
    session: AsyncSession,
    *,
    owner_id: str,
    paper_id: str,
    title: str,
    url: str | None = None,
    authors: dict[str, Any] | None = None,
    report_id: str | None = None,
) -> SavedPaper:
    """Insert-or-update a saved paper. Re-saving refreshes metadata."""
    saved = await session.scalar(
        select(SavedPaper).where(
            SavedPaper.owner_id == owner_id, SavedPaper.paper_id == paper_id
        )
    )
    if saved is None:
        saved = SavedPaper(
            owner_id=owner_id,
            paper_id=paper_id,
            title=title,
            url=url,
            authors=authors or {},
            report_id=report_id,
        )
        session.add(saved)
    else:
        saved.title = title or saved.title
        saved.url = url or saved.url
        if authors:
            saved.authors = authors
        if report_id:
            saved.report_id = report_id
    try:
        await session.commit()
    except IntegrityError:
        # Raced double-save hit the (owner_id, paper_id) unique constraint —
        # idempotent: the row exists either way, just refresh it.
        await session.rollback()
        saved = await session.scalar(
            select(SavedPaper).where(
                SavedPaper.owner_id == owner_id, SavedPaper.paper_id == paper_id
            )
        )
        if saved is None:  # pragma: no cover — shouldn't happen
            raise
    await session.refresh(saved)
    return saved


async def unsave_paper(session: AsyncSession, *, owner_id: str, paper_id: str) -> bool:
    result = await session.execute(
        delete(SavedPaper).where(
            SavedPaper.owner_id == owner_id, SavedPaper.paper_id == paper_id
        )
    )
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


async def get_saved_paper(
    session: AsyncSession, *, owner_id: str, paper_id: str
) -> SavedPaper | None:
    return await session.scalar(
        select(SavedPaper).where(
            SavedPaper.owner_id == owner_id, SavedPaper.paper_id == paper_id
        )
    )


async def list_saved_papers(
    session: AsyncSession, *, owner_id: str, status: str | None = None
) -> list[SavedPaper]:
    stmt = (
        select(SavedPaper)
        .where(SavedPaper.owner_id == owner_id)
        .order_by(SavedPaper.updated_at.desc())
    )
    if status:
        stmt = stmt.where(SavedPaper.status == status)
    return list((await session.scalars(stmt)).all())


async def set_saved_paper_status(
    session: AsyncSession, *, owner_id: str, paper_id: str, status: str
) -> SavedPaper | None:
    if status not in SavedPaper.READING_STATUSES:
        return None
    saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
    if saved is None:
        return None
    saved.status = status
    await session.commit()
    await session.refresh(saved)
    return saved


async def set_saved_paper_notion_page(
    session: AsyncSession,
    *,
    owner_id: str,
    paper_id: str,
    notion_page_id: str,
    synced_status: str | None = None,
) -> None:
    saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
    if saved is not None:
        saved.notion_page_id = notion_page_id
        if synced_status:
            saved.notion_synced_status = synced_status
        await session.commit()


async def set_saved_paper_notion_status(
    session: AsyncSession, *, owner_id: str, paper_id: str, status: str
) -> None:
    """Record the status value currently written in Notion (pull-back path)."""
    if status not in SavedPaper.READING_STATUSES:
        return
    saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
    if saved is not None:
        saved.status = status
        saved.notion_synced_status = status
        await session.commit()


async def list_saved_paper_ids(session: AsyncSession, *, owner_id: str) -> set[str]:
    return set(
        (
            await session.scalars(
                select(SavedPaper.paper_id).where(SavedPaper.owner_id == owner_id)
            )
        ).all()
    )


async def list_paper_chat_messages(
    session: AsyncSession, *, owner_id: str, paper_id: str, limit: int = 200
) -> list[ChatMessage]:
    """All chat messages for a paper across every thread/report, oldest
    first — the learning trail that gets mirrored into the Notion page."""
    result = await session.execute(
        select(ChatMessage)
        .join(ChatThread, ChatMessage.thread_id == ChatThread.id)
        .where(
            ChatThread.user_id == owner_id,
            ChatThread.paper_id == paper_id,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def set_saved_paper_notion_chat_count(
    session: AsyncSession, *, owner_id: str, paper_id: str, count: int
) -> None:
    """Watermark: how many chat messages already live on the Notion page."""
    saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
    if saved is not None:
        saved.notion_chat_synced = count
        await session.commit()


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

from services.persistence.models import Topic, TopicPaper  # noqa: E402


async def get_or_create_topic(
    session: AsyncSession,
    *,
    owner_id: str,
    kind: str,
    canonical_key: str,
    title: str,
    seed_query: str = "",
) -> tuple[Topic, bool]:
    """Return the topic for (owner, kind, canonical_key), creating it if absent."""
    topic = await session.scalar(
        select(Topic).where(
            Topic.owner_id == owner_id,
            Topic.kind == kind,
            Topic.canonical_key == canonical_key,
        )
    )
    if topic is not None:
        return topic, False
    topic = Topic(
        owner_id=owner_id,
        kind=kind,
        canonical_key=canonical_key,
        title=title[:300] or "Untitled topic",
        seed_query=seed_query,
    )
    session.add(topic)
    try:
        await session.flush()
    except IntegrityError:  # concurrent create — fetch the winner
        await session.rollback()
        topic = await session.scalar(
            select(Topic).where(
                Topic.owner_id == owner_id,
                Topic.kind == kind,
                Topic.canonical_key == canonical_key,
            )
        )
        return topic, False
    return topic, True


async def add_topic_papers(
    session: AsyncSession,
    *,
    topic_id: str,
    papers: list[Paper],
    source: str = "seed",
) -> int:
    """Insert papers into a topic's set, skipping dupes. Returns count added."""
    existing = set(
        (
            await session.execute(
                select(TopicPaper.paper_id).where(TopicPaper.topic_id == topic_id)
            )
        )
        .scalars()
        .all()
    )
    added = 0
    for paper in papers:
        if not paper.id or paper.id in existing:
            continue
        session.add(
            TopicPaper(
                topic_id=topic_id,
                paper_id=paper.id,
                title=paper.title or "Untitled paper",
                url=str(paper.url) if paper.url else None,
                authors={"items": paper.authors or []},
                published_date=(
                    str(paper.published_date) if paper.published_date else None
                ),
                source=source,
                metadata_json={
                    "score": paper.relevance_score,
                    "source": paper.source,
                },
            )
        )
        existing.add(paper.id)
        added += 1
    return added


async def list_topics(
    session: AsyncSession, *, owner_id: str, limit: int = 50
) -> list[Topic]:
    result = await session.execute(
        select(Topic)
        .where(Topic.owner_id == owner_id, Topic.archived.is_(False))
        .order_by(Topic.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_topic(
    session: AsyncSession, *, owner_id: str, topic_id: str
) -> Topic | None:
    return await session.scalar(
        select(Topic).where(Topic.id == topic_id, Topic.owner_id == owner_id)
    )


async def list_topic_papers(
    session: AsyncSession, *, topic_id: str
) -> list[TopicPaper]:
    result = await session.execute(
        select(TopicPaper)
        .where(TopicPaper.topic_id == topic_id)
        .order_by(TopicPaper.added_at.asc())
    )
    return list(result.scalars().all())


async def list_topic_reports(
    session: AsyncSession, *, topic_id: str, limit: int = 10
) -> list[Report]:
    result = await session.execute(
        select(Report)
        .where(Report.topic_id == topic_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def remove_topic_paper(
    session: AsyncSession, *, topic_id: str, paper_id: str
) -> bool:
    from sqlalchemy.engine import CursorResult

    result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(TopicPaper).where(
                TopicPaper.topic_id == topic_id, TopicPaper.paper_id == paper_id
            )
        ),
    )
    await session.commit()
    return int(result.rowcount or 0) > 0


async def set_report_topic(
    session: AsyncSession, *, report_id: str, topic_id: str
) -> None:
    report = await session.get(Report, report_id)
    if report is not None:
        report.topic_id = topic_id
        await session.commit()
