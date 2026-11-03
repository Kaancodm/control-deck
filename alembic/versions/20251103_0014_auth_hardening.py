"""Bind sessions to password epochs and persist bounded authentication observations."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20251103_0014"
down_revision = "20251103_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ui_sessions", sa.Column("last_active_at", sa.DateTime(timezone=True)))
    op.add_column("ui_sessions", sa.Column("password_fingerprint", sa.String(64)))
    op.execute("UPDATE ui_sessions SET last_active_at = created_at")
    op.execute(
        "UPDATE ui_sessions SET revoked_at = clock_timestamp() "
        "WHERE authenticated AND revoked_at IS NULL"
    )
    op.alter_column("ui_sessions", "last_active_at", nullable=False)
    op.add_column("ui_login_state", sa.Column("daily_failures", sa.Integer()))
    op.add_column("ui_login_state", sa.Column("day_started_at", sa.DateTime(timezone=True)))
    op.add_column("ui_login_state", sa.Column("locked_at", sa.DateTime(timezone=True)))
    op.add_column("ui_login_state", sa.Column("throttled_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE ui_login_state SET daily_failures = failures, day_started_at = window_started_at"
    )
    op.execute("UPDATE ui_login_state SET locked_at = clock_timestamp() WHERE daily_failures >= 20")
    op.alter_column("ui_login_state", "daily_failures", nullable=False)
    op.alter_column("ui_login_state", "day_started_at", nullable=False)
    op.create_check_constraint(
        "ck_ui_login_state_daily_failures_nonnegative", "ui_login_state", "daily_failures >= 0"
    )
    op.create_table(
        "auth_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column("affected_sessions", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('auth.login_failed', 'auth.login_throttled', 'auth.login_locked', "
            "'auth.login_succeeded', 'auth.logout', 'auth.sessions_revoked', "
            "'auth.login_unlocked')",
            name="ck_auth_events_type",
        ),
        sa.CheckConstraint("affected_sessions >= 0", name="ck_auth_events_count"),
    )
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])


def downgrade() -> None:
    op.execute("LOCK TABLE ui_sessions, ui_login_state, auth_events IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM auth_events) OR EXISTS "
            "(SELECT 1 FROM ui_login_state WHERE locked_at IS NOT NULL OR daily_failures > 0) "
            "OR EXISTS (SELECT 1 FROM ui_sessions WHERE authenticated AND revoked_at IS NULL "
            "AND expires_at > clock_timestamp())"
        )
    ):
        raise RuntimeError(
            "Downgrade blocked: authentication audit, sessions or protection state remain"
        )
    op.drop_index("ix_auth_events_created_at", table_name="auth_events")
    op.drop_table("auth_events")
    op.drop_constraint(
        "ck_ui_login_state_daily_failures_nonnegative", "ui_login_state", type_="check"
    )
    for column in ("throttled_at", "locked_at", "day_started_at", "daily_failures"):
        op.drop_column("ui_login_state", column)
    op.drop_column("ui_sessions", "password_fingerprint")
    op.drop_column("ui_sessions", "last_active_at")
