"""persist generated blog markdown on reports

Revision ID: 20261013_0006
Revises: 20261013_0004
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0006"
down_revision = "20261013_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("blog_markdown", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "blog_markdown")
