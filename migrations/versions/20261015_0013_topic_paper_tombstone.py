"""topic paper tombstones: soft-delete so feeds don't resurrect removals

Revision ID: 20261015_0013
Revises: 20261015_0012
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0013"
down_revision = "20261015_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("topic_papers")}
    # Idempotent: create_all at boot may already have applied this.
    if "removed_at" not in cols:
        op.add_column(
            "topic_papers",
            sa.Column("removed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("topic_papers", "removed_at")
