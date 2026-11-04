"""Separate trusted execution provenance and live evidence from simulation."""

import sqlalchemy as sa

from alembic import op

revision = "20261104_0018"
down_revision = "20261104_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old results have no attestation; never infer live execution from success.
    op.add_column(
        "results",
        sa.Column("execution_profile", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.alter_column("results", "execution_profile", server_default="simulation")
    op.create_check_constraint(
        "ck_results_execution_profile",
        "results",
        "execution_profile IN ('simulation', 'live', 'remote', 'unknown')",
    )
    op.add_column(
        "lane_status",
        sa.Column("simulated_attempts", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_lane_status_simulated_attempts", "lane_status", "simulated_attempts >= 0"
    )
    op.drop_constraint("ck_lane_status_counters", "lane_status", type_="check")
    op.create_check_constraint(
        "ck_lane_status_counters",
        "lane_status",
        "completed_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= completed_attempts + simulated_attempts",
    )
    # All earlier counters predate provenance. Retain their count as non-live,
    # preserving failures of any origin but discarding unattested live successes.
    op.execute(
        "UPDATE lane_status SET simulated_attempts=completed_attempts, completed_attempts=0, "
        "last_attempt_at=NULL, last_attempt_success_at=NULL"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE results, lane_status IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM results WHERE execution_profile <> 'unknown') + "
            "(SELECT count(*) FROM lane_status WHERE simulated_attempts <> 0)"
        )
    ):
        raise RuntimeError("Downgrade blocked: execution provenance evidence would be lost")
    op.drop_constraint("ck_lane_status_counters", "lane_status", type_="check")
    op.create_check_constraint(
        "ck_lane_status_counters",
        "lane_status",
        "completed_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= completed_attempts",
    )
    op.drop_constraint("ck_lane_status_simulated_attempts", "lane_status", type_="check")
    op.drop_column("lane_status", "simulated_attempts")
    op.drop_constraint("ck_results_execution_profile", "results", type_="check")
    op.drop_column("results", "execution_profile")
