"""saved papers: persistent library

Revision ID: 20261013_0003
Revises: 20261012_0002
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0003"
down_revision = "20261012_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_papers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("paper_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="to_read"
        ),
        sa.Column(
            "report_id",
            sa.String(length=36),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notion_page_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "paper_id", name="uq_saved_papers_owner_paper"),
    )
    op.create_index("ix_saved_papers_owner_id", "saved_papers", ["owner_id"])
    op.create_index("ix_saved_papers_status", "saved_papers", ["status"])
    op.create_index("ix_saved_papers_report_id", "saved_papers", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_papers_report_id", table_name="saved_papers")
    op.drop_index("ix_saved_papers_status", table_name="saved_papers")
    op.drop_index("ix_saved_papers_owner_id", table_name="saved_papers")
    op.drop_table("saved_papers")
