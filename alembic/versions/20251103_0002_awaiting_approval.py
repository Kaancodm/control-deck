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

_PRE_0002_TASK_STATES = (
    "'queued', 'leased', 'running', 'succeeded', 'failed', 'blocked', 'cancelled'"
)


def upgrade() -> None:
    # PostgreSQL requires the new enum label to be committed before it can be referenced.
    with op.get_context().autocommit_block():
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
    # In-flight work from 0001 already passed the implicit queue gate: grandfather approval.
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
    # Idle legacy queued rows without owner approval move to awaiting_approval.
    op.execute(
        sa.text(
            """
            UPDATE tasks
            SET status = 'awaiting_approval'
            WHERE status = 'queued' AND approved_at IS NULL
            """
        )
    )


def _count_awaiting_approval(bind: sa.Connection) -> int:
    tasks = bind.execute(
        sa.text("SELECT COUNT(*) FROM tasks WHERE status = 'awaiting_approval'")
    ).scalar_one()
    events = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM events
            WHERE from_status = 'awaiting_approval'
               OR to_status = 'awaiting_approval'
            """
        )
    ).scalar_one()
    return int(tasks) + int(events)


def _rebuild_task_state_without_awaiting_approval() -> None:
    op.execute(sa.text(f"CREATE TYPE task_state_old AS ENUM ({_PRE_0002_TASK_STATES})"))
    op.execute(sa.text("ALTER TABLE tasks ALTER COLUMN status DROP DEFAULT"))
    op.execute(
        sa.text(
            """
            ALTER TABLE tasks
            ALTER COLUMN status TYPE task_state_old
            USING status::text::task_state_old
            """
        )
    )
    op.execute(sa.text("ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'queued'"))
    op.execute(
        sa.text(
            """
            ALTER TABLE events
            ALTER COLUMN from_status TYPE task_state_old
            USING from_status::text::task_state_old
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE events
            ALTER COLUMN to_status TYPE task_state_old
            USING to_status::text::task_state_old
            """
        )
    )
    op.execute(sa.text("DROP TYPE task_state"))
    op.execute(sa.text("ALTER TYPE task_state_old RENAME TO task_state"))


def downgrade() -> None:
    bind = op.get_bind()
    if _count_awaiting_approval(bind) > 0:
        raise RuntimeError(
            "Downgrade blocked: tasks or audit events still reference "
            "awaiting_approval; approve, cancel, or migrate them first"
        )
    op.drop_column("tasks", "approved_at")
    op.drop_column("tasks", "requires_approval")
    try:
        _rebuild_task_state_without_awaiting_approval()
    except sa.exc.DBAPIError as exc:
        raise RuntimeError(
            "Downgrade blocked: task_state enum could not be restored to the "
            "pre-0002 value set safely"
        ) from exc
