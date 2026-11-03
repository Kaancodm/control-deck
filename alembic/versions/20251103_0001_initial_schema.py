"""Initial tasks, results, events, and leases schema.

Revision ID: 20251103_0001
Revises:
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20251103_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

task_state = sa.Enum(
    "queued",
    "leased",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    name="task_state",
    create_type=False,
)
data_class = sa.Enum(
    "local_only",
    "cloud_redacted",
    "cloud_ok",
    name="data_class",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE TYPE task_state AS ENUM ("
            "'queued', 'leased', 'running', 'succeeded', 'failed', 'blocked', 'cancelled'"
            ")"
        )
    )
    op.execute(
        sa.text("CREATE TYPE data_class AS ENUM ('local_only', 'cloud_redacted', 'cloud_ok')")
    )

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            task_state,
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "data_class",
            data_class,
            nullable=False,
            server_default="local_only",
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(payload->>'data_class') = data_class::text",
            name="ck_tasks_payload_data_class",
        ),
        sa.CheckConstraint(
            "(payload->>'task_id')::uuid = id",
            name="ck_tasks_payload_task_id",
        ),
    )
    op.create_index("ix_tasks_data_class", "tasks", ["data_class"], unique=False)
    op.create_index("ix_tasks_status_created_at", "tasks", ["status", "created_at"], unique=False)

    op.create_table(
        "results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("attempt >= 1", name="ck_results_attempt_min"),
    )
    op.create_index("ix_results_task_id_attempt", "results", ["task_id", "attempt"], unique=True)

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", task_state, nullable=True),
        sa.Column("to_status", task_state, nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_events_task_id_created_at", "events", ["task_id", "created_at"], unique=False
    )

    op.create_table(
        "leases",
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holder", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("lease_id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_leases_expires_at", "leases", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_leases_expires_at", table_name="leases")
    op.drop_table("leases")
    op.drop_index("ix_events_task_id_created_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_results_task_id_attempt", table_name="results")
    op.drop_table("results")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
    op.drop_index("ix_tasks_data_class", table_name="tasks")
    op.drop_table("tasks")
    op.execute(sa.text("DROP TYPE IF EXISTS data_class"))
    op.execute(sa.text("DROP TYPE IF EXISTS task_state"))
