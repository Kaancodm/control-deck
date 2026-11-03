"""Store opaque owner session digests and a shared login failure budget."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20251103_0012"
down_revision = "20251103_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("authenticated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ui_sessions_expires_at", "ui_sessions", ["expires_at"])
    op.create_table(
        "ui_login_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ui_login_state_singleton"),
        sa.CheckConstraint("failures >= 0", name="ck_ui_login_state_failures_nonnegative"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE ui_sessions, ui_login_state IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM ui_sessions WHERE authenticated AND revoked_at IS NULL "
            "AND expires_at > clock_timestamp()"
        )
    ):
        raise RuntimeError("Downgrade blocked: revoke active owner sessions first")
    op.drop_table("ui_login_state")
    op.drop_index("ix_ui_sessions_expires_at", table_name="ui_sessions")
    op.drop_table("ui_sessions")
