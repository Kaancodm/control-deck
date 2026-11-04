"""Persist per-client anonymous capacity and login-page request windows."""

from __future__ import annotations

import hashlib

import sqlalchemy as sa

from alembic import op

revision = "20261104_0017"
down_revision = "20251103_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ui_sessions", sa.Column("client_hash", sa.String(64), nullable=True))
    # Legacy anonymous sessions share the unknown-client bucket until normal expiry.
    op.get_bind().execute(
        sa.text("UPDATE ui_sessions SET client_hash = :digest WHERE NOT authenticated"),
        {"digest": hashlib.sha256(b"deck-login-client\0").hexdigest()},
    )
    op.create_index("ix_ui_sessions_client_hash", "ui_sessions", ["client_hash"])
    op.add_column(
        "ui_login_clients",
        sa.Column("page_requests", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ui_login_clients",
        sa.Column(
            "page_window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
    )
    op.create_check_constraint(
        "ck_ui_login_clients_page_requests_nonnegative", "ui_login_clients", "page_requests >= 0"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE ui_login_clients IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ui_login_clients "
            "WHERE page_requests > 0 AND page_window_started_at "
            "> clock_timestamp() - interval '1 hour')"
        )
    ):
        raise RuntimeError("Downgrade blocked: active login-page request windows remain")
    op.drop_constraint(
        "ck_ui_login_clients_page_requests_nonnegative", "ui_login_clients", type_="check"
    )
    op.drop_column("ui_login_clients", "page_window_started_at")
    op.drop_column("ui_login_clients", "page_requests")
    op.drop_index("ix_ui_sessions_client_hash", table_name="ui_sessions")
    op.drop_column("ui_sessions", "client_hash")
