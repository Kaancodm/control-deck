"""Correct approval gate data for DBs that applied the original 0002 backfill.

Revision ID: 20251103_0004
Revises: 20251103_0003
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20251103_0004"
down_revision: str | Sequence[str] | None = "20251103_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: safe on fresh DBs and on DBs that ran the old 0002 mass auto-approve.
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET approved_at = COALESCE(approved_at, created_at)
            WHERE approved_at IS NULL
              AND status IN ('leased', 'running')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'awaiting_approval',
                requires_approval = true,
                approved_at = NULL
            WHERE status = 'queued'
              AND approved_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'awaiting_approval',
                requires_approval = true,
                approved_at = NULL
            WHERE status = 'queued'
              AND requires_approval = false
              AND approved_at IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    regated = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM tasks
            WHERE status = 'awaiting_approval'
              AND requires_approval = true
              AND approved_at IS NULL
            """
        )
    ).scalar_one()
    if regated and int(regated) > 0:
        raise RuntimeError(
            "Downgrade blocked: cannot restore pre-0004 approval rows safely; "
            "approve or cancel awaiting_approval tasks first"
        )
