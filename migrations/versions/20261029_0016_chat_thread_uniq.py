"""chat_threads: dedupe + full-scope unique index

Revision ID: 20261029_0016
Revises: 20261015_0015
Create Date: 2026-10-29

Thread identity is the 4-tuple (user_id, report_id, paper_id, topic_id) —
that's what the repo functions match on. The old constraint only covered
(user_id, report_id, paper_id) and, because NULLs are distinct in Postgres,
topic threads (report_id NULL) could silently duplicate.

Dedupes existing rows by repointing messages to the oldest thread in each
scope, then installs a UNIQUE index over the coalesced tuple so NULL scope
fields compare equal.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0016"
down_revision = "20261015_0015"
branch_labels = None
depends_on = None

_INDEX = "uq_chat_threads_scope"


def upgrade() -> None:
    if "chat_threads" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return

    # Repoint messages off duplicate threads onto the oldest in each scope.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id,
                                        COALESCE(report_id, ''),
                                        paper_id,
                                        COALESCE(topic_id, '')
                           ORDER BY created_at ASC, id ASC
                       ) AS rn,
                       FIRST_VALUE(id) OVER (
                           PARTITION BY user_id,
                                        COALESCE(report_id, ''),
                                        paper_id,
                                        COALESCE(topic_id, '')
                           ORDER BY created_at ASC, id ASC
                       ) AS keep_id
                FROM chat_threads
            )
            UPDATE chat_messages m
               SET thread_id = r.keep_id
              FROM ranked r
             WHERE m.thread_id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id,
                                        COALESCE(report_id, ''),
                                        paper_id,
                                        COALESCE(topic_id, '')
                           ORDER BY created_at ASC, id ASC
                       ) AS rn
                FROM chat_threads
            )
            DELETE FROM chat_threads
             WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        )
    )

    # The old 3-col unique constraint can't coexist with the wider index.
    constraints = {
        c["name"]
        for c in sa.inspect(op.get_bind()).get_unique_constraints("chat_threads")
    }
    for name in constraints:
        if name != _INDEX:
            op.drop_constraint(name, "chat_threads", type_="unique")

    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON chat_threads "
            "(user_id, COALESCE(report_id, ''), paper_id, "
            "COALESCE(topic_id, ''))"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX}"))
    op.create_unique_constraint(
        "chat_threads_user_id_report_id_paper_id_key",
        "chat_threads",
        ["user_id", "report_id", "paper_id"],
    )
