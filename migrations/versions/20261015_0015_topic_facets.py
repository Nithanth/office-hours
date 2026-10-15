"""add topic_facets — multiple search angles per topic

Revision ID: 20261015_0015
Revises: 20261015_0014
Create Date: 2026-10-15

A topic is a research question; facets are the different queries/vocabularies
into it. Feeds poll every facet and merge new papers into the shared set.

Idempotent: dev boots run create_all before alembic sees the DB, so the
CREATE TABLE is gated on the table being absent and the backfill only fills
facets for topics that have none.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20261015_0015"
down_revision = "20261015_0014"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if "topics" not in tables:
        return
    if "topic_facets" not in tables:
        op.create_table(
            "topic_facets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "topic_id",
                sa.String(36),
                sa.ForeignKey("topics.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("kind", sa.String(16), nullable=False, server_default="subject"),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("canonical_key", sa.String(512), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("topic_id", "kind", "canonical_key"),
        )
    # create_all boots may have built the table (and index) ahead of us —
    # add the index only when it isn't there yet.
    if "ix_topic_facets_topic_id" not in _indexes("topic_facets"):
        op.create_index("ix_topic_facets_topic_id", "topic_facets", ["topic_id"])

    # Backfill: every existing topic gets its seed query as facet zero.
    conn = op.get_bind()
    topics = conn.execute(
        sa.text("SELECT id, kind, canonical_key, seed_query, title FROM topics")
    ).fetchall()
    for tid, kind, ckey, seed, title in topics:
        has = conn.execute(
            sa.text("SELECT 1 FROM topic_facets WHERE topic_id = :t LIMIT 1"),
            {"t": tid},
        ).first()
        if has:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO topic_facets (id, topic_id, kind, query, "
                "canonical_key, created_at) VALUES (:i, :t, :k, :q, :c, :d)"
            ),
            {
                "i": str(_uuid.uuid4()),
                "t": tid,
                "k": kind or "subject",
                "q": seed or title or "",
                "c": ckey or (seed or title or "").lower()[:500],
                "d": datetime.utcnow(),
            },
        )


def downgrade() -> None:
    if "topic_facets" in _tables():
        op.drop_table("topic_facets")
