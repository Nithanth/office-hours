"""notion topics: body-content hash for change-detected re-sync

Revision ID: 20261015_0012
Revises: 20261015_0011
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0012"
down_revision = "20261015_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("topics")}
    # Idempotent: create_all at boot may already have applied this.
    if "notion_body_hash" not in cols:
        op.add_column(
            "topics", sa.Column("notion_body_hash", sa.String(64), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("topics", "notion_body_hash")
