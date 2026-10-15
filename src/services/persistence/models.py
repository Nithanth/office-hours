"""SQLAlchemy models for users, history, integrations, and usage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from services.persistence.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


JSONType = JSON().with_variant(JSONB, "postgresql")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_user_id: Mapped[str] = mapped_column(String(255), index=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Topic(Base):
    """A research topic a user follows — the product's unit of work.

    Papers are a topic's contents (`topic_papers`); reports are versioned
    snapshots rendered from the paper set. ``owner_id`` follows the owner-key
    convention (auth ``user_id`` or ``"sess:{session_id}"``), matching
    ``SavedPaper``/``UserPreference``.

    ``kind`` + ``canonical_key`` are the topic's identity: the query string is
    only the seed. "INR" and "implicit neural representations" should converge
    on one subject topic rather than forking.
    """

    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("owner_id", "kind", "canonical_key"),)

    KINDS = ("subject", "paper", "author")

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16), default="subject")
    canonical_key: Mapped[str] = mapped_column(String(512), index=True)
    seed_query: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    notion_page_id: Mapped[str | None] = mapped_column(String(64))
    # Topic-scoped workspace outputs — one canonical blog + notes per topic;
    # snapshots keep their own frozen copy on reports.blog_markdown.
    blog_markdown: Mapped[str | None] = mapped_column(Text)
    notes_markdown: Mapped[str | None] = mapped_column(Text)
    # Last time the feed polled external sources for this topic (None = never).
    last_feed_check: Mapped[datetime | None] = mapped_column(DateTime)
    # sha256 of the markdown body last mirrored to the Notion page — lets the
    # sync skip rewrites when blog/notes haven't changed.
    notion_body_hash: Mapped[str | None] = mapped_column(String(64))

    papers: Mapped[list[TopicPaper]] = relationship(back_populates="topic")


class TopicPaper(Base):
    """A paper belonging to a topic's curated set."""

    __tablename__ = "topic_papers"
    __table_args__ = (UniqueConstraint("topic_id", "paper_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[dict] = mapped_column(JSONType, default=dict)
    published_date: Mapped[str | None] = mapped_column(String(64))
    # How it joined: 'seed' (initial search), 'saved' (library), 'feed' (v2),
    # 'manual' (added by hand).
    source: Mapped[str] = mapped_column(String(16), default="seed")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Soft-delete tombstone: set when the user removes the paper so feed
    # polls don't resurrect it. Explicit (non-feed) adds clear it.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict] = mapped_column(JSONType, default=dict)

    topic: Mapped[Topic] = relationship(back_populates="papers")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Owner key, not a users.id FK: the auth user_id when signed in, otherwise
    # "sess:{session_id}" so anonymous visitors own their reports (same
    # convention as UserPreference.owner_id / SavedPaper.owner_id).
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    original_query: Mapped[str] = mapped_column(Text)
    audience_type: Mapped[str] = mapped_column(String(64))
    summary_size: Mapped[str] = mapped_column(String(8))
    model_key: Mapped[str] = mapped_column(String(100))
    is_url_input: Mapped[bool] = mapped_column(Boolean, default=False)
    report_html: Mapped[str] = mapped_column(Text)
    report_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    notion_page_id: Mapped[str | None] = mapped_column(String(64))
    blog_markdown: Mapped[str | None] = mapped_column(Text)
    # The topic this report is a snapshot of (SET NULL so deleting a topic
    # keeps the report history).
    topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), index=True
    )

    papers: Mapped[list[ReportPaper]] = relationship(back_populates="report")


class ReportPaper(Base):
    __tablename__ = "report_papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[dict] = mapped_column(JSONType, default=dict)
    published_date: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSONType, default=dict)

    report: Mapped[Report] = relationship(back_populates="papers")


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (UniqueConstraint("user_id", "report_id", "paper_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Owner key (see Report.user_id): auth user_id or "sess:{session_id}".
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    # Topic chat that survives report re-renders (a report thread keeps
    # report_id; a topic-level thread keys on this instead).
    topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(String(255), default="global")
    title: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class UserPreference(Base):
    """Per-owner settings row.

    ``owner_id`` is the auth ``user_id`` when signed in, otherwise
    ``"sess:{session_id}"`` for anonymous visitors (the signed oh_sid
    cookie), so preferences work without an account.
    """

    __tablename__ = "user_preferences"

    owner_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    # {"fast": "author/slug", "standard": ..., "deep": ...} — sparse;
    # missing tiers fall back to the pinned config defaults.
    model_pins: Mapped[dict] = mapped_column(JSONType, default=dict)
    openrouter_key_encrypted: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL")
    )
    export_type: Mapped[str] = mapped_column(String(32))
    destination: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    bot_id: Mapped[str | None] = mapped_column(String(100))
    workspace_id: Mapped[str | None] = mapped_column(String(100))
    workspace_name: Mapped[str | None] = mapped_column(String(255))
    workspace_icon: Mapped[str | None] = mapped_column(Text)
    parent_page_id: Mapped[str | None] = mapped_column(String(100))
    parent_database_id: Mapped[str | None] = mapped_column(String(100))
    topics_database_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SavedPaper(Base):
    """A paper in a user's persistent library.

    ``owner_id`` mirrors ``UserPreference``: the auth ``user_id`` when
    signed in, otherwise ``"sess:{session_id}"`` for anonymous visitors.
    ``paper_id`` is the same identifier used everywhere else in the app
    (typically the source URL), so it joins to chat threads and vector
    indexes without translation.
    """

    __tablename__ = "saved_papers"
    __table_args__ = (UniqueConstraint("owner_id", "paper_id"),)

    READING_STATUSES = ("to_read", "reading", "done")

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    paper_id: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="to_read", index=True)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL"), index=True
    )
    notion_page_id: Mapped[str | None] = mapped_column(String(64))
    # Status value we last saw written in Notion — lets a sync tell which
    # side changed (app vs Notion) instead of blindly letting one win.
    notion_synced_status: Mapped[str | None] = mapped_column(String(32))
    # Count of chat messages already appended to the Notion page body —
    # a watermark so re-syncs only push new Q&A turns.
    notion_chat_synced: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Owner key (see Report.user_id), nullable: auth user_id, "sess:{id}", or
    # None for unattributed events.
    user_id: Mapped[str | None] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
