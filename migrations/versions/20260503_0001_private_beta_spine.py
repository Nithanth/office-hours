"""private beta SaaS spine

Revision ID: 20260503_0001
Revises:
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260503_0001"
down_revision = None
branch_labels = None
depends_on = None


def _json_type() -> sa.JSON:
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id"),
    )
    op.create_index("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("audience_type", sa.String(length=64), nullable=False),
        sa.Column("summary_size", sa.String(length=8), nullable=False),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("is_url_input", sa.Boolean(), nullable=False),
        sa.Column("report_html", sa.Text(), nullable=False),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])
    op.create_index("ix_reports_created_at", "reports", ["created_at"])

    op.create_table(
        "report_papers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "report_id", sa.String(length=36), sa.ForeignKey("reports.id", ondelete="CASCADE")
        ),
        sa.Column("paper_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("authors", _json_type(), nullable=False),
        sa.Column("published_date", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", _json_type(), nullable=False),
    )
    op.create_index("ix_report_papers_report_id", "report_papers", ["report_id"])
    op.create_index("ix_report_papers_paper_id", "report_papers", ["paper_id"])

    op.create_table(
        "chat_threads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "report_id",
            sa.String(length=36),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("paper_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "report_id", "paper_id"),
    )
    op.create_index("ix_chat_threads_user_id", "chat_threads", ["user_id"])
    op.create_index("ix_chat_threads_report_id", "chat_threads", ["report_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "thread_id", sa.String(length=36), sa.ForeignKey("chat_threads.id", ondelete="CASCADE")
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])

    op.create_table(
        "exports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "report_id",
            sa.String(length=36),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("export_type", sa.String(length=32), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_exports_user_id", "exports", ["user_id"])
    op.create_index("ix_exports_created_at", "exports", ["created_at"])

    op.create_table(
        "user_integrations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("bot_id", sa.String(length=100), nullable=True),
        sa.Column("workspace_id", sa.String(length=100), nullable=True),
        sa.Column("workspace_name", sa.String(length=255), nullable=True),
        sa.Column("workspace_icon", sa.Text(), nullable=True),
        sa.Column("parent_page_id", sa.String(length=100), nullable=True),
        sa.Column("parent_database_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "provider"),
    )
    op.create_index("ix_user_integrations_user_id", "user_integrations", ["user_id"])

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_event_type", "usage_events", ["event_type"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    for table in [
        "usage_events",
        "user_integrations",
        "exports",
        "chat_messages",
        "chat_threads",
        "report_papers",
        "reports",
        "oauth_accounts",
        "users",
    ]:
        op.drop_table(table)
