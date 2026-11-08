"""usage_events.client_ip — per-IP anonymous quota bucket

Revision ID: 20261108_0017
Revises: 20261029_0016
Create Date: 2026-11-08

Anonymous daily caps were keyed only on the resettable oh_sid cookie. The
quota reservation now also counts anonymous events per client IP.

Idempotent: dev boots run create_all before alembic sees the DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261108_0017"
down_revision = "20261029_0016"
branch_labels = None
depends_on = None

_INDEX = "ix_usage_events_client_ip"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "usage_events" not in set(insp.get_table_names()):
        return
    columns = {c["name"] for c in insp.get_columns("usage_events")}
    if "client_ip" not in columns:
        op.add_column(
            "usage_events", sa.Column("client_ip", sa.String(64), nullable=True)
        )
    indexes = {ix["name"] for ix in insp.get_indexes("usage_events")}
    if _INDEX not in indexes:
        op.create_index(_INDEX, "usage_events", ["client_ip"])


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX}"))
    op.drop_column("usage_events", "client_ip")
