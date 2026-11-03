"""Add safe per-lane health observations and retained last-success timestamps."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20251103_0010"
down_revision = "20251103_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lane_status",
        sa.Column("lane", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("lane IN ('ollama', 'claude', 'codex')", name="ck_lane_status_lane"),
        sa.CheckConstraint(
            "status IN ('ok', 'unavailable', 'auth_required', 'paused')",
            name="ck_lane_status_status",
        ),
        sa.CheckConstraint("reason ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_lane_status_reason"),
        sa.PrimaryKeyConstraint("lane"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE lane_status IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM lane_status")):
        raise RuntimeError("Downgrade blocked: lane health observations would be lost")
    op.drop_table("lane_status")
