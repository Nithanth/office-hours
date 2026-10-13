"""notion library sync: topics db id + report notion page id

Revision ID: 20261013_0004
Revises: 20261013_0003
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0004"
down_revision = "20261013_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_integrations",
        sa.Column("topics_database_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("notion_page_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "notion_page_id")
    op.drop_column("user_integrations", "topics_database_id")
