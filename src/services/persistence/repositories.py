"""Repository helpers for the private-beta persistence layer."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
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
    UsageEvent,
    User,
    UserIntegration,
)


def html_to_text(value: str) -> str:
    clean = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", "", value, flags=re.I)
    clean = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", "", clean, flags=re.I)
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
        account.access_token_encrypted = encrypted_access or account.access_token_encrypted
        account.refresh_token_encrypted = encrypted_refresh or account.refresh_token_encrypted
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


async def daily_usage_count(session: AsyncSession, *, user_id: str, event_type: str) -> int:
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
) -> Report:
    report = Report(
        user_id=user_id,
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
                published_date=str(paper.published_date) if paper.published_date else None,
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


async def list_reports(session: AsyncSession, *, user_id: str, limit: int = 50) -> list[Report]:
    result = await session.execute(
        select(Report)
        .where(Report.user_id == user_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_report(session: AsyncSession, *, user_id: str, report_id: str) -> Report | None:
    return await session.scalar(
        select(Report).where(Report.id == report_id, Report.user_id == user_id)
    )


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
        thread = ChatThread(user_id=user_id, report_id=report_id, paper_id=paper_id, title=title)
        session.add(thread)
        await session.flush()
    else:
        thread.updated_at = datetime.utcnow()
    message = ChatMessage(thread_id=thread.id, role=role, content=content)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


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
