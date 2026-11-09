"""Store one completion wake per task, separate from the task state machine.

Revision ID: 20261108_0063
Revises: 20261108_0062
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261108_0063"
down_revision = "20261108_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routine_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("task_status", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "event", name="uq_routine_outbox_task_event"),
        sa.UniqueConstraint("correlation_id", name="uq_routine_outbox_correlation"),
        sa.CheckConstraint("event = 'task.completed'", name="ck_routine_outbox_event"),
        sa.CheckConstraint(
            "schema_version = 'deck.wake.v1'",
            name="ck_routine_outbox_version",
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_routine_outbox_payload_hash",
        ),
        sa.CheckConstraint(
            "task_status IN ('review', 'done')",
            name="ck_routine_outbox_task_status",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'sending', 'accepted', 'start_failed', "
            "'unknown_start', 'acknowledged')",
            name="ck_routine_outbox_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_routine_outbox_attempts",
        ),
        sa.CheckConstraint(
            "(state IN ('accepted', 'acknowledged') AND accepted_at IS NOT NULL) "
            "OR (state NOT IN ('accepted', 'acknowledged') AND accepted_at IS NULL)",
            name="ck_routine_outbox_accepted",
        ),
        sa.CheckConstraint(
            "(state = 'acknowledged' AND acknowledged_at IS NOT NULL) "
            "OR (state <> 'acknowledged' AND acknowledged_at IS NULL)",
            name="ck_routine_outbox_acknowledged",
        ),
    )
    op.create_index(
        "ix_routine_outbox_dispatch",
        "routine_outbox",
        ["created_at"],
        postgresql_where=sa.text("state IN ('pending', 'start_failed')"),
    )


def downgrade() -> None:
    op.drop_index("ix_routine_outbox_dispatch", table_name="routine_outbox")
    op.drop_table("routine_outbox")
