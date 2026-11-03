"""Add review, done, expired task states and owner deadline metadata.

Revision ID: 20251103_0007
Revises: 20251103_0006
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20251103_0007"
down_revision: str | Sequence[str] | None = "20251103_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_STATES = "'review', 'done', 'expired'"
_PRE_0007_STATES = (
    "'queued', 'leased', 'running', 'succeeded', 'failed', 'blocked', "
    "'cancelled', 'awaiting_approval'"
)


def upgrade() -> None:
    # Existing succeeded rows remain historical; new results use review or done.
    with op.get_context().autocommit_block():
        for state in ("review", "done", "expired"):
            op.execute(sa.text(f"ALTER TYPE task_state ADD VALUE IF NOT EXISTS '{state}'"))
    op.add_column("tasks", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    referenced = bind.execute(
        sa.text(
            f"""
            SELECT (
                SELECT COUNT(*) FROM tasks WHERE status IN ({_NEW_STATES})
            ) + (
                SELECT COUNT(*) FROM events
                WHERE from_status IN ({_NEW_STATES}) OR to_status IN ({_NEW_STATES})
            )
            """
        )
    ).scalar_one()
    if referenced:
        raise RuntimeError(
            "Downgrade blocked: tasks or audit events still reference review, done, or expired"
        )
    if bind.execute(
        sa.text("SELECT COUNT(*) FROM tasks WHERE deadline_at IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("Downgrade blocked: owner deadlines would be lost")

    op.execute(sa.text(f"CREATE TYPE task_state_pre_0007 AS ENUM ({_PRE_0007_STATES})"))
    op.execute(sa.text("ALTER TABLE tasks ALTER COLUMN status DROP DEFAULT"))
    for table, column in (("tasks", "status"), ("events", "from_status"), ("events", "to_status")):
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {column} TYPE task_state_pre_0007 "
                f"USING {column}::text::task_state_pre_0007"
            )
        )
    op.execute(sa.text("DROP TYPE task_state"))
    op.execute(sa.text("ALTER TYPE task_state_pre_0007 RENAME TO task_state"))
    op.execute(sa.text("ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'queued'"))
    op.drop_column("tasks", "deadline_at")
