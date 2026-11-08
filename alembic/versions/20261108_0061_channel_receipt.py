"""Store one channel acceptance receipt per task.

The assistant chain also descends from 20261104_0018. Revision 20261108_0062
joins the two branches. This file stays the receipt branch and is not rebased.

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261108_0061"
down_revision = "20261104_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", name="uq_channel_receipts_task_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_channel_receipts_idempotency_key"),
        sa.CheckConstraint(
            "status IN ('accepted', 'delivered', 'deadline_elapsed')",
            name="ck_channel_receipts_status",
        ),
        sa.CheckConstraint(
            "transport = 'missing' OR transport ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="ck_channel_receipts_transport",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$'",
            name="ck_channel_receipts_idempotency_key",
        ),
        sa.CheckConstraint(
            "deadline_at = accepted_at + interval '60 seconds'",
            name="ck_channel_receipts_deadline",
        ),
        sa.CheckConstraint(
            "(status = 'delivered' AND transport <> 'missing' AND delivered_at IS NOT NULL) "
            "OR (status <> 'delivered' AND delivered_at IS NULL)",
            name="ck_channel_receipts_delivery",
        ),
    )
    op.create_index(
        "ix_channel_receipts_deadline",
        "channel_receipts",
        ["deadline_at"],
        postgresql_where=sa.text("status = 'accepted'"),
    )


def downgrade() -> None:
    op.drop_index("ix_channel_receipts_deadline", table_name="channel_receipts")
    op.drop_table("channel_receipts")
