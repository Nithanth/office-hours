"""drop users FKs on owner-key columns (anonymous ownership)

reports.user_id, chat_threads.user_id and usage_events.user_id now store an
owner key — the auth user_id when signed in, otherwise "sess:{session_id}" —
so they can no longer be foreign keys into users.id.

Revision ID: 20261013_0008
Revises: 20261013_0007
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0008"
down_revision = "20261013_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("reports_user_id_fkey", "reports", type_="foreignkey")
    op.alter_column("reports", "user_id", type_=sa.String(80), existing_nullable=False)
    op.drop_constraint("chat_threads_user_id_fkey", "chat_threads", type_="foreignkey")
    op.alter_column(
        "chat_threads", "user_id", type_=sa.String(80), existing_nullable=False
    )
    op.drop_constraint("usage_events_user_id_fkey", "usage_events", type_="foreignkey")
    op.alter_column(
        "usage_events", "user_id", type_=sa.String(80), existing_nullable=True
    )


def downgrade() -> None:
    # Re-adding the FKs fails while any "sess:" owner rows exist; clear them
    # first if you ever downgrade a populated database.
    op.alter_column(
        "usage_events", "user_id", type_=sa.String(36), existing_nullable=True
    )
    op.create_foreign_key(
        "usage_events_user_id_fkey",
        "usage_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "chat_threads", "user_id", type_=sa.String(36), existing_nullable=False
    )
    op.create_foreign_key(
        "chat_threads_user_id_fkey",
        "chat_threads",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("reports", "user_id", type_=sa.String(36), existing_nullable=False)
    op.create_foreign_key(
        "reports_user_id_fkey",
        "reports",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
