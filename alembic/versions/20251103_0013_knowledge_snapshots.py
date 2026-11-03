"""Bind local context snapshots and durable owner export intents to tasks."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0013"
down_revision = "20251103_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_context_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("reference", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_class", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "position", name="uq_context_snapshot_position"),
        sa.UniqueConstraint("task_id", "reference", name="uq_context_snapshot_reference"),
        sa.CheckConstraint("position BETWEEN 0 AND 15", name="ck_context_snapshot_position"),
        sa.CheckConstraint(
            "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')",
            name="ck_context_snapshot_class",
        ),
        sa.CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 25024", name="ck_context_snapshot_size"
        ),
        sa.CheckConstraint(
            "content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
            name="ck_context_snapshot_hash",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_context_snapshot_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Approved context snapshots are immutable'; END;
        $$
    """)
    op.execute(
        "CREATE TRIGGER immutable_context_snapshot BEFORE UPDATE ON task_context_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION reject_context_snapshot_update()"
    )
    op.create_table(
        "knowledge_exports",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_class", sa.String(32), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("document_id"),
        sa.CheckConstraint(
            "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')",
            name="ck_knowledge_export_class",
        ),
        sa.CheckConstraint("state IN ('pending', 'completed')", name="ck_knowledge_export_state"),
        sa.CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="ck_knowledge_export_hash"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE knowledge_exports, task_context_snapshots IN ACCESS EXCLUSIVE MODE")
    count = op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM knowledge_exports) + "
            "(SELECT count(*) FROM task_context_snapshots)"
        )
    )
    if count:
        raise RuntimeError("Downgrade blocked: knowledge snapshots or export intents would be lost")
    op.drop_table("knowledge_exports")
    op.drop_table("task_context_snapshots")
    op.execute("DROP FUNCTION reject_context_snapshot_update()")
