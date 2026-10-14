"""topics as first-class objects + report/chat topic links

Revision ID: 20261014_0009
Revises: 20261013_0008
Create Date: 2026-10-14
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261014_0009"
down_revision = "20261013_0008"
branch_labels = None
depends_on = None


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _has_fk(bind, table: str, fk: str) -> bool:
    return fk in {
        fk_["name"]
        for fk_ in sa.inspect(bind).get_foreign_keys(table)
        if fk_.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # Idempotent: envs where create_all already ran at boot may have the new
    # tables/columns without an alembic stamp.
    if "topics" not in tables:
        op.create_table(
            "topics",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(80), index=True),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="subject"),
            sa.Column("canonical_key", sa.String(512), nullable=False),
            sa.Column("seed_query", sa.Text, server_default=""),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.Column("archived", sa.Boolean, server_default=sa.text("false")),
            sa.Column("notion_page_id", sa.String(64), nullable=True),
            sa.UniqueConstraint("owner_id", "kind", "canonical_key"),
        )
    if not _has_index(bind, "topics", "ix_topics_canonical_key"):
        op.create_index("ix_topics_canonical_key", "topics", ["canonical_key"])

    if "topic_papers" not in tables:
        op.create_table(
            "topic_papers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "topic_id",
                sa.String(36),
                sa.ForeignKey("topics.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("paper_id", sa.String(255), nullable=False),
            sa.Column("title", sa.Text, nullable=False),
            sa.Column("url", sa.Text, nullable=True),
            sa.Column("authors", sa.JSON, server_default="{}"),
            sa.Column("published_date", sa.String(64), nullable=True),
            sa.Column("source", sa.String(16), server_default="seed"),
            sa.Column("added_at", sa.DateTime, nullable=False),
            sa.Column("metadata_json", sa.JSON, server_default="{}"),
            sa.UniqueConstraint("topic_id", "paper_id"),
        )
    if not _has_index(bind, "topic_papers", "ix_topic_papers_topic_id"):
        op.create_index("ix_topic_papers_topic_id", "topic_papers", ["topic_id"])
    if not _has_index(bind, "topic_papers", "ix_topic_papers_paper_id"):
        op.create_index("ix_topic_papers_paper_id", "topic_papers", ["paper_id"])

    def _cols(table: str) -> set[str]:
        return {c["name"] for c in sa.inspect(bind).get_columns(table)}

    if "topic_id" not in _cols("reports"):
        op.add_column("reports", sa.Column("topic_id", sa.String(36), nullable=True))
    if not _has_index(bind, "reports", "ix_reports_topic_id"):
        op.create_index("ix_reports_topic_id", "reports", ["topic_id"])
    if not _has_fk(bind, "reports", "reports_topic_id_fkey"):
        op.create_foreign_key(
            "reports_topic_id_fkey",
            "reports",
            "topics",
            ["topic_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if "topic_id" not in _cols("chat_threads"):
        op.add_column(
            "chat_threads", sa.Column("topic_id", sa.String(36), nullable=True)
        )
    if not _has_index(bind, "chat_threads", "ix_chat_threads_topic_id"):
        op.create_index("ix_chat_threads_topic_id", "chat_threads", ["topic_id"])
    if not _has_fk(bind, "chat_threads", "chat_threads_topic_id_fkey"):
        op.create_foreign_key(
            "chat_threads_topic_id_fkey",
            "chat_threads",
            "topics",
            ["topic_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill: wrap every existing report in a topic seeded from its papers.
    reports = bind.execute(
        sa.text(
            "SELECT id, user_id, title, original_query, is_url_input, created_at"
            " FROM reports WHERE topic_id IS NULL"
        )
    ).fetchall()
    for r in reports:
        topic_id = str(uuid.uuid4())
        kind = "paper" if r.is_url_input else "subject"
        canonical = (r.original_query or "").strip().lower()[:512] or r.id
        row = bind.execute(
            sa.text(
                "INSERT INTO topics (id, owner_id, title, kind, canonical_key,"
                " seed_query, created_at, updated_at, archived)"
                " VALUES (:id, :owner, :title, :kind, :ck, :sq, :ca, :ua, false)"
                " ON CONFLICT (owner_id, kind, canonical_key) DO NOTHING"
                " RETURNING id"
            ),
            {
                "id": topic_id,
                "owner": r.user_id,
                "title": (r.title or "Untitled topic")[:300],
                "kind": kind,
                "ck": canonical,
                "sq": r.original_query or "",
                "ca": r.created_at,
                "ua": r.created_at,
            },
        ).fetchone()
        if row is None:
            # Same owner+kind+canonical already has a topic — converge reports
            # onto it instead of failing the unique key.
            row = bind.execute(
                sa.text(
                    "SELECT id FROM topics WHERE owner_id = :owner AND kind = :kind"
                    " AND canonical_key = :ck"
                ),
                {"owner": r.user_id, "kind": kind, "ck": canonical},
            ).fetchone()
            if row is None:
                continue
        topic_id = row[0]
        bind.execute(
            sa.text(
                "INSERT INTO topic_papers (id, topic_id, paper_id, title, url,"
                " authors, published_date, source, added_at, metadata_json)"
                " SELECT md5(:tid || '-' || id), :tid, paper_id, title, url,"
                " authors, published_date, 'seed', :ca, metadata_json"
                " FROM report_papers WHERE report_id = :rid"
                " ON CONFLICT (topic_id, paper_id) DO NOTHING"
            ),
            {"tid": topic_id, "rid": r.id, "ca": r.created_at},
        )
        bind.execute(
            sa.text("UPDATE reports SET topic_id = :tid WHERE id = :rid"),
            {"tid": topic_id, "rid": r.id},
        )


def downgrade() -> None:
    op.drop_constraint("chat_threads_topic_id_fkey", "chat_threads", type_="foreignkey")
    op.drop_index("ix_chat_threads_topic_id", "chat_threads")
    op.drop_column("chat_threads", "topic_id")
    op.drop_constraint("reports_topic_id_fkey", "reports", type_="foreignkey")
    op.drop_index("ix_reports_topic_id", "reports")
    op.drop_column("reports", "topic_id")
    op.drop_table("topic_papers")
    op.drop_table("topics")
