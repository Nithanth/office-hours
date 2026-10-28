"""Repository helpers for the private-beta persistence layer."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
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
    topic_id: str | None = None,
) -> ChatMessage:
    thread = await session.scalar(
        select(ChatThread).where(
            ChatThread.user_id == user_id,
            ChatThread.report_id == report_id,
            ChatThread.paper_id == paper_id,
            ChatThread.topic_id == topic_id,
        )
    )
    if thread is None:
        thread = ChatThread(
            user_id=user_id,
            report_id=report_id,
            paper_id=paper_id,
            title=title,
            topic_id=topic_id,
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
    topic_id: str | None = None,
) -> list[ChatMessage]:
    """Persisted chat messages for a thread, oldest first."""
    thread = await session.scalar(
        select(ChatThread).where(
            ChatThread.user_id == user_id,
            ChatThread.report_id == report_id,
            ChatThread.paper_id == paper_id,
            ChatThread.topic_id == topic_id,
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


async def list_notion_integrations(
    session: AsyncSession,
) -> list[UserIntegration]:
    """Every Notion connection — the comment sweep scans these."""
    result = await session.execute(
        select(UserIntegration).where(UserIntegration.provider == "notion")
    )
    return list(result.scalars().all())


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
    expected_token: str | None = None,
) -> None:
    """Persist provisioned Notion database ids on the user's integration.

    ``expected_token`` guards the write against a disconnect+reconnect that
    lands mid-sync: when given, the row is only written if the integration's
    stored token still matches the one the sync provisioned under.
    """
    integration = await get_notion_integration(session, user_id=user_id)
    if integration is not None:
        if expected_token is not None and (
            notion_access_token(integration) != expected_token
        ):
            return
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
    # Drop the page ids pointing at the old workspace — rows re-provision
    # fresh pages if the user reconnects (same or different workspace).
    # Preserve updated_at: it's recency, not a Notion change.
    await session.execute(
        update(SavedPaper)
        .where(SavedPaper.owner_id == user_id)
        .values(
            notion_page_id=None,
            notion_synced_status=None,
            notion_chat_synced=0,
            updated_at=SavedPaper.updated_at,
        )
    )
    await session.execute(
        update(Report)
        .where(Report.user_id == user_id)
        .values(notion_page_id=None, updated_at=Report.updated_at)
    )
    await session.execute(
        update(Topic)
        .where(Topic.owner_id == user_id)
        .values(
            notion_page_id=None,
            notion_body_hash=None,
            updated_at=Topic.updated_at,
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

from services.persistence.models import Topic, TopicFacet, TopicPaper  # noqa: E402


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


async def list_topic_facets(
    session: AsyncSession, *, topic_id: str
) -> list[TopicFacet]:
    """All search angles of a topic, oldest first."""
    result = await session.execute(
        select(TopicFacet)
        .where(TopicFacet.topic_id == topic_id)
        .order_by(TopicFacet.created_at)
    )
    return list(result.scalars().all())


async def add_topic_facet(
    session: AsyncSession,
    *,
    topic_id: str,
    kind: str,
    query: str,
    canonical_key: str,
) -> tuple[TopicFacet, bool]:
    """Add a search angle to a topic. Returns (facet, created)."""
    facet = await session.scalar(
        select(TopicFacet).where(
            TopicFacet.topic_id == topic_id,
            TopicFacet.kind == kind,
            TopicFacet.canonical_key == canonical_key,
        )
    )
    if facet is not None:
        return facet, False
    facet = TopicFacet(
        topic_id=topic_id, kind=kind, query=query, canonical_key=canonical_key
    )
    session.add(facet)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        facet = await session.scalar(
            select(TopicFacet).where(
                TopicFacet.topic_id == topic_id,
                TopicFacet.kind == kind,
                TopicFacet.canonical_key == canonical_key,
            )
        )
        return facet, False
    return facet, True


async def remove_topic_facet(
    session: AsyncSession, *, facet_id: str, topic_id: str
) -> bool:
    """Delete a facet (never the last one — a topic needs >=1 angle)."""
    facets = await list_topic_facets(session, topic_id=topic_id)
    if len(facets) <= 1:
        return False
    facet = await session.get(TopicFacet, facet_id)
    if facet is None or facet.topic_id != topic_id:
        return False
    await session.delete(facet)
    return True


async def ensure_seed_facet(session: AsyncSession, *, topic: Topic) -> None:
    """Guarantee the topic's seed query exists as a facet (create-time)."""
    if await list_topic_facets(session, topic_id=topic.id):
        return
    await add_topic_facet(
        session,
        topic_id=topic.id,
        kind=topic.kind,
        query=topic.seed_query or topic.title,
        canonical_key=topic.canonical_key,
    )


async def merge_topics(
    session: AsyncSession, *, source_id: str, into_id: str, owner_id: str
) -> Topic | None:
    """Merge an adjacent topic into another: facets + papers + reports move,
    source is archived. Returns the surviving topic or None on bad input."""
    if source_id == into_id:
        return None
    source = await session.get(Topic, source_id)
    target = await session.get(Topic, into_id)
    if (
        source is None
        or target is None
        or source.owner_id != owner_id
        or target.owner_id != owner_id
    ):
        return None

    from services.topics.keys import canonical_key_for

    for f in await list_topic_facets(session, topic_id=source.id):
        await add_topic_facet(
            session,
            topic_id=target.id,
            kind=f.kind,
            query=f.query,
            canonical_key=f.canonical_key or canonical_key_for(f.query, kind=f.kind),
        )
    # Repoint papers the target doesn't already have (unique constraint on
    # topic_id+paper_id means dupes are skipped via a fetch-then-add walk).
    target_paper_ids = {
        p.paper_id for p in await list_topic_papers(session, topic_id=target.id)
    }
    for tp in await list_topic_papers(session, topic_id=source.id):
        if tp.paper_id in target_paper_ids:
            continue
        session.add(
            TopicPaper(
                topic_id=target.id,
                paper_id=tp.paper_id,
                title=tp.title,
                url=tp.url,
                authors=tp.authors,
                published_date=tp.published_date,
                source=tp.source,
                removed_at=tp.removed_at,
                metadata_json=tp.metadata_json,
            )
        )
    # Move snapshot reports so history follows the merge.
    await session.execute(
        update(Report).where(Report.topic_id == source.id).values(topic_id=target.id)
    )
    source.archived = True
    target.updated_at = datetime.utcnow()
    return target


async def add_topic_papers(
    session: AsyncSession,
    *,
    topic_id: str,
    papers: list[Paper],
    source: str = "seed",
) -> int:
    """Insert papers into a topic's set, skipping dupes. Returns count added.

    Papers the user removed (removed_at tombstone) are never resurrected by
    feed polls — but an explicit add (seed/manual/saved) DOES bring one back,
    clearing the tombstone.
    """
    rows = list(
        (
            await session.execute(
                select(TopicPaper.paper_id, TopicPaper.removed_at).where(
                    TopicPaper.topic_id == topic_id
                )
            )
        ).all()
    )
    existing = {r.paper_id for r in rows if r.removed_at is None}
    tombstoned = {r.paper_id for r in rows if r.removed_at is not None}
    added = 0
    for paper in papers:
        if not paper.id or paper.id in existing:
            continue
        if paper.id in tombstoned:
            if source == "feed":
                continue  # user removed it; the feed doesn't get to undo that
            await session.execute(
                update(TopicPaper)
                .where(
                    TopicPaper.topic_id == topic_id,
                    TopicPaper.paper_id == paper.id,
                )
                .values(removed_at=None, source=source)
            )
            tombstoned.discard(paper.id)
            existing.add(paper.id)
            added += 1
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
        .where(
            TopicPaper.topic_id == topic_id,
            TopicPaper.removed_at.is_(None),
        )
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


async def topic_card_stats(
    session: AsyncSession, *, owner_id: str
) -> dict[str, dict[str, Any]]:
    """Per-topic card data for the topics index: live paper count plus the
    latest report's id and date — two grouped queries instead of N+1."""
    paper_rows = await session.execute(
        select(TopicPaper.topic_id, func.count())
        .join(Topic, Topic.id == TopicPaper.topic_id)
        .where(Topic.owner_id == owner_id, TopicPaper.removed_at.is_(None))
        .group_by(TopicPaper.topic_id)
    )
    out: dict[str, dict[str, Any]] = {
        str(tid): {"papers": n, "report_id": None, "report_at": None}
        for tid, n in paper_rows.all()
    }
    report_rows = await session.execute(
        select(Report.topic_id, Report.id, Report.created_at)
        .join(Topic, Topic.id == Report.topic_id)
        .where(Topic.owner_id == owner_id)
        .order_by(Report.created_at.desc())
    )
    for tid, rid, created in report_rows.all():
        slot = out.setdefault(
            str(tid), {"papers": 0, "report_id": None, "report_at": None}
        )
        if slot["report_id"] is None:
            slot["report_id"] = rid
            slot["report_at"] = created
    return out


async def remove_topic_paper(
    session: AsyncSession, *, topic_id: str, paper_id: str
) -> bool:
    """Soft-delete: stamp removed_at so the feed won't resurrect the paper."""
    from sqlalchemy.engine import CursorResult

    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(TopicPaper)
            .where(
                TopicPaper.topic_id == topic_id,
                TopicPaper.paper_id == paper_id,
                TopicPaper.removed_at.is_(None),
            )
            .values(removed_at=datetime.utcnow())
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


async def set_topic_blog(
    session: AsyncSession, *, owner_id: str, topic_id: str, markdown: str
) -> Topic | None:
    """Persist the canonical blog on the topic (workspace scope)."""
    topic = await get_topic(session, owner_id=owner_id, topic_id=topic_id)
    if topic is None:
        return None
    topic.blog_markdown = markdown
    await session.commit()
    await session.refresh(topic)
    return topic


async def set_topic_notes(
    session: AsyncSession, *, owner_id: str, topic_id: str, markdown: str
) -> Topic | None:
    """Persist generated study notes on the topic (workspace scope)."""
    topic = await get_topic(session, owner_id=owner_id, topic_id=topic_id)
    if topic is None:
        return None
    topic.notes_markdown = markdown
    await session.commit()
    await session.refresh(topic)
    return topic


async def set_topic_notion(
    session: AsyncSession,
    *,
    owner_id: str,
    topic_id: str,
    notion_page_id: str | None = None,
    notion_body_hash: str | None = None,
) -> None:
    """Persist Notion sync bookkeeping on a topic (idempotent fields set
    only when provided)."""
    topic = await get_topic(session, owner_id=owner_id, topic_id=topic_id)
    if topic is None:
        return
    if notion_page_id is not None:
        topic.notion_page_id = notion_page_id
    if notion_body_hash is not None:
        topic.notion_body_hash = notion_body_hash
    await session.commit()


async def get_topic_by_share_token(
    session: AsyncSession, *, token: str
) -> Topic | None:
    """Public lookup — the token IS the capability, no owner check."""
    return await session.scalar(select(Topic).where(Topic.share_token == token))


async def set_topic_share_token(
    session: AsyncSession, *, owner_id: str, topic_id: str, token: str | None
) -> None:
    topic = await get_topic(session, owner_id=owner_id, topic_id=topic_id)
    if topic is None:
        return
    topic.share_token = token
    await session.commit()


async def get_report_topic_id(session: AsyncSession, *, report_id: str) -> str | None:
    """Resolve a report to its parent topic id (None if unlinked/missing)."""
    return await session.scalar(select(Report.topic_id).where(Report.id == report_id))


# ---------------------------------------------------------------------------
# Anonymous → authenticated ownership claim
# ---------------------------------------------------------------------------

_STATUS_RANK = {"to_read": 0, "reading": 1, "done": 2}


async def claim_anonymous_session(
    session: AsyncSession, *, user_id: str, session_id: str
) -> dict[str, int]:
    """Reassign everything owned by ``sess:{session_id}`` to ``user_id``.

    Runs once per OAuth sign-in: the anonymous visitor's topics, reports,
    chat history, library, and preferences become the account's — nothing
    is silently orphaned at the owner-key swap. Idempotent; safe to re-run.

    Collision policy (the account's existing rows always win):
    - topics: same (kind, canonical_key) → merge papers/reports/threads into
      the account's topic, carry over content fields the account's lacks.
    - chat_threads: same (report_id, paper_id, topic_id) → move messages
      into the account's thread.
    - saved_papers: same paper_id → keep the account's row, take the more
      advanced reading status and any Notion links it lacks.
    - user_preferences: merge pins (account wins) + keep the account's BYOK
      key unless it has none.
    """
    anon = f"sess:{session_id}"
    stats: dict[str, int] = {}

    # --- topics: reassign, or merge into the account's topic on key hit ---
    sess_topics: list[Topic] = list(
        (await session.execute(select(Topic).where(Topic.owner_id == anon)))
        .scalars()
        .all()
    )
    for topic in sess_topics:
        existing = await session.scalar(
            select(Topic).where(
                Topic.owner_id == user_id,
                Topic.kind == topic.kind,
                Topic.canonical_key == topic.canonical_key,
            )
        )
        if existing is None:
            topic.owner_id = user_id
            stats["topics"] = stats.get("topics", 0) + 1
            continue

        paper_rows: list[TopicPaper] = list(
            (
                await session.execute(
                    select(TopicPaper).where(TopicPaper.topic_id == topic.id)
                )
            )
            .scalars()
            .all()
        )
        existing_paper_ids = set(
            (
                await session.execute(
                    select(TopicPaper.paper_id).where(
                        TopicPaper.topic_id == existing.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for tp in paper_rows:
            if tp.paper_id in existing_paper_ids:
                await session.execute(delete(TopicPaper).where(TopicPaper.id == tp.id))
            else:
                tp.topic_id = existing.id
                existing_paper_ids.add(tp.paper_id)

        for field in ("blog_markdown", "notes_markdown", "seed_query"):
            if not getattr(existing, field) and getattr(topic, field):
                setattr(existing, field, getattr(topic, field))
        if not existing.notion_page_id and topic.notion_page_id:
            existing.notion_page_id = topic.notion_page_id
        existing.updated_at = datetime.utcnow()

        await session.execute(
            update(Report)
            .where(Report.topic_id == topic.id)
            .values(topic_id=existing.id)
        )
        await session.execute(
            update(ChatThread)
            .where(ChatThread.topic_id == topic.id)
            .values(topic_id=existing.id)
        )
        await session.execute(delete(Topic).where(Topic.id == topic.id))
        stats["topics_merged"] = stats.get("topics_merged", 0) + 1

    # --- reports: plain owner-key reassignment (topic ids already repointed) ---
    await session.execute(
        update(Report).where(Report.user_id == anon).values(user_id=user_id)
    )

    # --- chat threads: reassign, or merge messages on key hit ---
    sess_threads: list[ChatThread] = list(
        (await session.execute(select(ChatThread).where(ChatThread.user_id == anon)))
        .scalars()
        .all()
    )
    for thread in sess_threads:
        existing = await session.scalar(
            select(ChatThread).where(
                ChatThread.user_id == user_id,
                ChatThread.report_id == thread.report_id,
                ChatThread.paper_id == thread.paper_id,
                ChatThread.topic_id == thread.topic_id,
            )
        )
        if existing is None:
            thread.user_id = user_id
            stats["chat_threads"] = stats.get("chat_threads", 0) + 1
            continue
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .values(thread_id=existing.id)
        )
        if not existing.title and thread.title:
            existing.title = thread.title
        existing.updated_at = datetime.utcnow()
        await session.execute(delete(ChatThread).where(ChatThread.id == thread.id))
        stats["chat_threads_merged"] = stats.get("chat_threads_merged", 0) + 1

    # --- library: reassign, or dedupe on (owner_id, paper_id) ---
    sess_saves: list[SavedPaper] = list(
        (await session.execute(select(SavedPaper).where(SavedPaper.owner_id == anon)))
        .scalars()
        .all()
    )
    for saved in sess_saves:
        existing = await session.scalar(
            select(SavedPaper).where(
                SavedPaper.owner_id == user_id,
                SavedPaper.paper_id == saved.paper_id,
            )
        )
        if existing is None:
            saved.owner_id = user_id
            stats["saved_papers"] = stats.get("saved_papers", 0) + 1
            continue
        if _STATUS_RANK.get(saved.status, 0) > _STATUS_RANK.get(existing.status, 0):
            existing.status = saved.status
            existing.notion_synced_status = saved.notion_synced_status
        if not existing.notion_page_id and saved.notion_page_id:
            existing.notion_page_id = saved.notion_page_id
            existing.notion_chat_synced = saved.notion_chat_synced
        if existing.report_id is None and saved.report_id:
            existing.report_id = saved.report_id
        existing.updated_at = datetime.utcnow()
        await session.execute(delete(SavedPaper).where(SavedPaper.id == saved.id))
        stats["saved_papers_deduped"] = stats.get("saved_papers_deduped", 0) + 1

    # --- preferences: reassign, or merge (account's pins/key win) ---
    sess_pref = await session.scalar(
        select(UserPreference).where(UserPreference.owner_id == anon)
    )
    if sess_pref is not None:
        user_pref = await session.scalar(
            select(UserPreference).where(UserPreference.owner_id == user_id)
        )
        if user_pref is None:
            sess_pref.owner_id = user_id
        else:
            merged = dict(sess_pref.model_pins or {})
            merged.update(user_pref.model_pins or {})
            user_pref.model_pins = merged
            if (
                not user_pref.openrouter_key_encrypted
                and sess_pref.openrouter_key_encrypted
            ):
                user_pref.openrouter_key_encrypted = sess_pref.openrouter_key_encrypted
            user_pref.updated_at = datetime.utcnow()
            await session.execute(
                delete(UserPreference).where(UserPreference.owner_id == anon)
            )
        stats["preferences"] = stats.get("preferences", 0) + 1

    # --- usage events: plain reassign ---
    await session.execute(
        update(UsageEvent).where(UsageEvent.user_id == anon).values(user_id=user_id)
    )

    await session.commit()
    return stats


async def home_stats(session: AsyncSession, *, owner_id: str) -> dict[str, int]:
    """Counts for the home research surface: saved papers by status,
    topics, reports."""
    saved_rows = await session.execute(
        select(SavedPaper.status, func.count())
        .where(SavedPaper.owner_id == owner_id)
        .group_by(SavedPaper.status)
    )
    by_status: dict[str, int] = {
        str(status): cast(int, n) for status, n in saved_rows.all()
    }
    topics_n = (
        await session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(Topic.owner_id == owner_id, Topic.archived.is_(False))
        )
        or 0
    )
    reports_n = (
        await session.scalar(
            select(func.count()).select_from(Report).where(Report.user_id == owner_id)
        )
        or 0
    )
    return {
        "saved": sum(by_status.values()),
        "to_read": by_status.get("to_read", 0),
        "reading": by_status.get("reading", 0),
        "done": by_status.get("done", 0),
        "topics": int(topics_n),
        "reports": int(reports_n),
    }


async def feed_new_counts(
    session: AsyncSession, *, topic_ids: list[str], days: int = 7
) -> dict[str, int]:
    """topic_id -> count of feed-sourced papers added in the last ``days``."""
    if not topic_ids:
        return {}
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = await session.execute(
        select(TopicPaper.topic_id, func.count())
        .where(
            TopicPaper.topic_id.in_(topic_ids),
            TopicPaper.source == "feed",
            TopicPaper.removed_at.is_(None),
            TopicPaper.added_at >= cutoff,
        )
        .group_by(TopicPaper.topic_id)
    )
    return {str(tid): cast(int, n) for tid, n in rows.all()}
