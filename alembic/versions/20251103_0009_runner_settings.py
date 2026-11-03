"""Add local runner pause, stop, lane and concurrency controls."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0009"
down_revision = "20251103_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE settings IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM settings")):
        raise RuntimeError("Downgrade blocked: runner control settings would be lost")
    op.drop_table("settings")
