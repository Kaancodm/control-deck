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
    for name in ("simulated_attempts", "remote_attempts", "unknown_attempts", "probe_generation"):
        op.add_column(
            "lane_status",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"),
        )
        op.create_check_constraint(f"ck_lane_status_{name}", "lane_status", f"{name} >= 0")
    op.drop_constraint("ck_lane_status_counters", "lane_status", type_="check")
    op.create_check_constraint(
        "ck_lane_status_counters",
        "lane_status",
        "completed_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= completed_attempts + simulated_attempts "
        "+ remote_attempts + unknown_attempts",
    )
    # Historical counts have unknown provenance, never simulated provenance.
    # Live completion/success evidence starts here; retain the failure history.
    op.execute(
        "UPDATE lane_status SET unknown_attempts=completed_attempts, completed_attempts=0, "
        "last_attempt_at=NULL, last_attempt_success_at=NULL"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE results, lane_status IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM results WHERE execution_profile <> 'unknown') + "
            "(SELECT count(*) FROM lane_status WHERE simulated_attempts <> 0 "
            "OR remote_attempts <> 0 OR unknown_attempts <> 0 OR probe_generation <> 0 "
            "OR completed_attempts <> 0 OR consecutive_failures <> 0 "
            "OR last_error_code IS NOT NULL OR last_error_at IS NOT NULL "
            "OR last_attempt_at IS NOT NULL OR last_attempt_success_at IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Downgrade blocked: execution provenance or accepted attempt evidence would be lost"
        )
    op.drop_constraint("ck_lane_status_counters", "lane_status", type_="check")
    for name in ("simulated_attempts", "remote_attempts", "unknown_attempts", "probe_generation"):
        op.drop_constraint(f"ck_lane_status_{name}", "lane_status", type_="check")
        op.drop_column("lane_status", name)
    op.create_check_constraint(
        "ck_lane_status_counters",
        "lane_status",
        "completed_attempts >= 0 AND consecutive_failures >= 0 "
        "AND consecutive_failures <= completed_attempts",
    )
    op.drop_constraint("ck_results_execution_profile", "results", type_="check")
    op.drop_column("results", "execution_profile")
