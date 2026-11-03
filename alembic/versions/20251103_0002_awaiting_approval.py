"""Add awaiting_approval task state for owner approval gate.

Revision ID: 20251103_0002
Revises: 20251103_0001
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20251103_0002"
down_revision: str | Sequence[str] | None = "20251103_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'awaiting_approval'"))
    op.add_column(
        "tasks",
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill pre-approval rows (0001 only created queued/… tasks; no awaiting_approval yet).
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET requires_approval = false,
                approved_at = COALESCE(approved_at, NOW())
            """
        )
    )


def downgrade() -> None:
    op.drop_column("tasks", "approved_at")
    op.drop_column("tasks", "requires_approval")
    # PostgreSQL does not support removing enum values; awaiting_approval remains on downgrade.
