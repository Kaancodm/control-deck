"""Count accepted lane attempts separately from positive health observations."""

import sqlalchemy as sa

from alembic import op

revision = "20251103_0016"
down_revision = "20251103_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Counters start at this migration; historical observations are not executions.
    for name in ("completed_attempts", "consecutive_failures"):
        op.add_column(
            "lane_status", sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
        )
    op.add_column("lane_status", sa.Column("last_error_code", sa.String(32), nullable=True))
    for name in ("last_error_at", "last_attempt_at", "last_attempt_success_at"):
        op.add_column("lane_status", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_lane_status_counters",
        "lane_status",
        "completed_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= completed_attempts",
    )
    op.create_check_constraint(
        "ck_lane_status_error_code",
        "lane_status",
        "last_error_code IN ('worker_unavailable', 'auth_required', 'rate_limited', "
        "'timeout', 'policy_blocked', 'acceptance_failed', 'internal')",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE lane_status IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM lane_status WHERE completed_attempts <> 0 "
            "OR consecutive_failures <> 0 OR last_error_code IS NOT NULL "
            "OR last_error_at IS NOT NULL OR last_attempt_at IS NOT NULL "
            "OR last_attempt_success_at IS NOT NULL"
        )
    ):
        raise RuntimeError("Downgrade blocked: accepted attempt evidence would be lost")
    op.drop_constraint("ck_lane_status_error_code", "lane_status", type_="check")
    op.drop_constraint("ck_lane_status_counters", "lane_status", type_="check")
    for name in (
        "last_attempt_success_at",
        "last_attempt_at",
        "last_error_at",
        "last_error_code",
        "consecutive_failures",
        "completed_attempts",
    ):
        op.drop_column("lane_status", name)
