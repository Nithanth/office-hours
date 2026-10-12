"""user preferences: model pins and BYOK openrouter key

Revision ID: 20261012_0002
Revises: 20260503_0001
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261012_0002"
down_revision = "20260503_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("owner_id", sa.String(length=80), primary_key=True),
        sa.Column("model_pins", sa.JSON(), nullable=False),
        sa.Column("openrouter_key_encrypted", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
