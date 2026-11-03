"""Add append-only taskless maintenance observations."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0015"
down_revision = "20251103_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operations_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "operation_type IN ('backup', 'restore', 'retention', 'update')",
            name="ck_operations_events_type",
        ),
        sa.CheckConstraint(
            "phase IN ('started', 'artifacts_removed', 'completed', 'failed')",
            name="ck_operations_events_phase",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operations_events_operation_id", "operations_events", ["operation_id", "id"]
    )
    op.create_index("ix_operations_events_created_at", "operations_events", ["created_at", "id"])
    op.execute("""
        CREATE FUNCTION control_deck_operations_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Maintenance events are append-only';
        END; $$;
        CREATE TRIGGER operations_events_append_only
        BEFORE UPDATE OR DELETE ON operations_events
        FOR EACH ROW EXECUTE FUNCTION control_deck_operations_append_only();
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE operations_events IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM operations_events")):
        raise RuntimeError("Downgrade blocked: maintenance evidence would be lost")
    op.execute("DROP TRIGGER operations_events_append_only ON operations_events")
    op.execute("DROP FUNCTION control_deck_operations_append_only()")
    op.drop_index("ix_operations_events_created_at", table_name="operations_events")
    op.drop_index("ix_operations_events_operation_id", table_name="operations_events")
    op.drop_table("operations_events")
