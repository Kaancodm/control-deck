"""DB integrity triggers, cancel flags, and stricter CHECK constraints.

Revision ID: 20251103_0003
Revises: 20251103_0002
Create Date: 2025-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20251103_0003"
down_revision: str | Sequence[str] | None = "20251103_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("tasks", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("tasks", sa.Column("lane", sa.String(length=64), nullable=True))

    op.add_column("events", sa.Column("actor", sa.String(length=32), nullable=True))
    op.add_column("events", sa.Column("attempt", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.drop_constraint("events_task_id_fkey", "events", type_="foreignkey")
    op.create_foreign_key(
        "events_task_id_fkey",
        "events",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint("ck_tasks_payload_data_class", "tasks", type_="check")
    op.drop_constraint("ck_tasks_payload_task_id", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_payload_schema_version",
        "tasks",
        "payload->>'schema_version' = 'task.v1'",
    )
    op.create_check_constraint(
        "ck_tasks_payload_data_class",
        "tasks",
        "(payload->>'data_class') IS NOT NULL AND (payload->>'data_class') = data_class::text",
    )
    op.create_check_constraint(
        "ck_tasks_payload_task_id",
        "tasks",
        "(payload->>'task_id') IS NOT NULL AND (payload->>'task_id')::uuid = id",
    )

    op.alter_column("tasks", "data_class", server_default=None)

    op.create_check_constraint(
        "ck_results_payload_schema_version",
        "results",
        "payload->>'schema_version' = 'result.v1'",
    )
    op.create_check_constraint(
        "ck_results_payload_task_id",
        "results",
        "(payload->>'task_id') IS NOT NULL AND (payload->>'task_id')::uuid = task_id",
    )
    op.create_check_constraint(
        "ck_results_payload_attempt",
        "results",
        "(payload->>'attempt') IS NOT NULL AND (payload->>'attempt')::int = attempt",
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION tasks_immutable_payload()
            RETURNS trigger AS $$
            BEGIN
              IF NEW.id IS DISTINCT FROM OLD.id
                 OR NEW.payload IS DISTINCT FROM OLD.payload
                 OR NEW.data_class IS DISTINCT FROM OLD.data_class
                 OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'tasks row is immutable (id, payload, data_class, created_at)';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_tasks_immutable_payload
            BEFORE UPDATE ON tasks
            FOR EACH ROW EXECUTE FUNCTION tasks_immutable_payload();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION events_append_only()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'events table is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_events_append_only
            BEFORE UPDATE OR DELETE ON events
            FOR EACH ROW EXECUTE FUNCTION events_append_only();
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_events_append_only ON events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS events_append_only()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_tasks_immutable_payload ON tasks"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS tasks_immutable_payload()"))

    op.drop_constraint("ck_results_payload_attempt", "results", type_="check")
    op.drop_constraint("ck_results_payload_task_id", "results", type_="check")
    op.drop_constraint("ck_results_payload_schema_version", "results", type_="check")

    op.alter_column("tasks", "data_class", server_default="local_only")

    op.drop_constraint("ck_tasks_payload_task_id", "tasks", type_="check")
    op.drop_constraint("ck_tasks_payload_data_class", "tasks", type_="check")
    op.drop_constraint("ck_tasks_payload_schema_version", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_payload_data_class",
        "tasks",
        "(payload->>'data_class') = data_class::text",
    )
    op.create_check_constraint(
        "ck_tasks_payload_task_id",
        "tasks",
        "(payload->>'task_id')::uuid = id",
    )

    op.drop_constraint("events_task_id_fkey", "events", type_="foreignkey")
    op.create_foreign_key(
        "events_task_id_fkey",
        "events",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_column("events", "lease_id")
    op.drop_column("events", "attempt")
    op.drop_column("events", "actor")
    op.drop_column("tasks", "lane")
    op.drop_column("tasks", "plan")
    op.drop_column("tasks", "cancel_reason")
    op.drop_column("tasks", "cancel_requested")
