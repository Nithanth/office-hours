"""topic feed bookkeeping: last_feed_check marker

Revision ID: 20261015_0011
Revises: 20261015_0010
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0011"
down_revision = "20261015_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("topics")}
    # Idempotent: create_all at boot may already have applied this.
    if "last_feed_check" not in cols:
        op.add_column(
            "topics", sa.Column("last_feed_check", sa.DateTime, nullable=True)
        )


def downgrade() -> None:
    op.drop_column("topics", "last_feed_check")
