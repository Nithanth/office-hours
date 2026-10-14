"""topic-scoped workspace outputs: blog + notes on topics, chat thread backfill

Revision ID: 20261015_0010
Revises: 20261014_0009
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0010"
down_revision = "20261014_0009"
branch_labels = None
depends_on = None


def _cols(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # Idempotent: create_all at boot may already have applied these.
    if "blog_markdown" not in _cols(bind, "topics"):
        op.add_column("topics", sa.Column("blog_markdown", sa.Text, nullable=True))
    if "notes_markdown" not in _cols(bind, "topics"):
        op.add_column("topics", sa.Column("notes_markdown", sa.Text, nullable=True))

    # Backfill canonical topic blog from each topic's latest snapshot blog.
    bind.execute(
        sa.text(
            "UPDATE topics SET blog_markdown = ("
            "  SELECT blog_markdown FROM reports"
            "  WHERE reports.topic_id = topics.id"
            "    AND reports.blog_markdown IS NOT NULL"
            "  ORDER BY created_at DESC LIMIT 1"
            ") WHERE blog_markdown IS NULL"
        )
    )

    # Backfill chat thread topic links from their reports (column shipped
    # in 0009 but only filled for new threads).
    bind.execute(
        sa.text(
            "UPDATE chat_threads SET topic_id = ("
            "  SELECT reports.topic_id FROM reports"
            "  WHERE reports.id = chat_threads.report_id"
            ") WHERE topic_id IS NULL AND report_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("topics", "notes_markdown")
    op.drop_column("topics", "blog_markdown")
