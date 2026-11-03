"""Persist bounded retry progress and the next eligible attempt time."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0011"
down_revision = "20251103_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("retry_state", postgresql.JSONB(), nullable=True))
    op.add_column("tasks", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_tasks_queued_next_attempt",
        "tasks",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM tasks "
            "WHERE retry_state IS NOT NULL OR next_attempt_at IS NOT NULL"
        )
    ):
        raise RuntimeError("Downgrade blocked: task retry progress or backoff would be lost")
    op.drop_index("ix_tasks_queued_next_attempt", table_name="tasks")
    op.drop_column("tasks", "next_attempt_at")
    op.drop_column("tasks", "retry_state")
