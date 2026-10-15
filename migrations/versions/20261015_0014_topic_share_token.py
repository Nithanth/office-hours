"""add topics.share_token for public share links

Revision ID: 20261015_0014
Revises: 20261015_0013
Create Date: 2026-10-15

Idempotent: dev boots run create_all before alembic sees the DB, so every
operation is gated on the column being absent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0014"
down_revision = "20261015_0013"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "topics" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "share_token" not in _columns("topics"):
        op.add_column("topics", sa.Column("share_token", sa.String(64), nullable=True))


def downgrade() -> None:
    if "topics" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "share_token" in _columns("topics"):
        op.drop_column("topics", "share_token")
