"""Add immutable task-scoped local redaction maps."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0008"
down_revision = "20251103_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redaction_maps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("placeholder", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("original", sa.Text(), nullable=False),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("first_attempt", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("task_id", "placeholder", name="uq_redaction_maps_task_placeholder"),
        sa.UniqueConstraint(
            "task_id", "original_sha256", name="uq_redaction_maps_task_original_hash"
        ),
        sa.CheckConstraint(
            "kind IN ('EMAIL', 'PHONE', 'IBAN', 'AHV', 'IP', 'TERM')", name="ck_redaction_maps_kind"
        ),
        sa.CheckConstraint("first_attempt >= 1", name="ck_redaction_maps_first_attempt"),
        sa.CheckConstraint("original <> ''", name="ck_redaction_maps_original_not_empty"),
        sa.CheckConstraint(
            "original_sha256 = encode(sha256(convert_to(original, 'UTF8')), 'hex')",
            name="ck_redaction_maps_original_hash",
        ),
        sa.CheckConstraint(
            r"placeholder ~ ('^\[\[CD_' || replace(task_id::text, '-', '') || '_' || "
            r"kind || '_[1-9][0-9]{0,9}\]\]$')",
            name="ck_redaction_maps_placeholder_scope",
        ),
    )
    op.create_index("ix_redaction_maps_task_id", "redaction_maps", ["task_id"])
    op.execute(
        """
        CREATE FUNCTION control_deck_redaction_maps_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'redaction map entries are immutable';
        END;
        $$;
        CREATE TRIGGER redaction_maps_immutable
        BEFORE UPDATE ON redaction_maps
        FOR EACH ROW EXECUTE FUNCTION control_deck_redaction_maps_immutable();
        """
    )


def downgrade() -> None:
    op.execute("LOCK TABLE redaction_maps IN ACCESS EXCLUSIVE MODE")
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM redaction_maps"))
    if count:
        raise RuntimeError("Downgrade blocked: local redaction maps would be lost")
    op.execute("DROP TRIGGER redaction_maps_immutable ON redaction_maps")
    op.execute("DROP FUNCTION control_deck_redaction_maps_immutable()")
    op.drop_index("ix_redaction_maps_task_id", table_name="redaction_maps")
    op.drop_table("redaction_maps")
