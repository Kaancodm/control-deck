"""Allow a loopback dialogue origin beside the mock.

Downgrade refuses when a non-mock row exists. It does not delete those rows.

Revision ID: 20261108_0020
Revises: 20261108_0019
Create Date: 2026-11-08
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "20261108_0020"
down_revision = "20261108_0019"
branch_labels = None
depends_on = None

_ORIGINS = "reply_origin IN ('mock', 'ollama_loopback')"


def upgrade() -> None:
    op.drop_constraint("ck_assistant_turns_origin_mock", "assistant_turns", type_="check")
    op.create_check_constraint("ck_assistant_turns_origin", "assistant_turns", _ORIGINS)


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(
        text("SELECT count(*) FROM assistant_turns WHERE reply_origin <> 'mock'")
    ).scalar_one()
    if remaining:
        raise RuntimeError("assistant_turn_origin")
    op.drop_constraint("ck_assistant_turns_origin", "assistant_turns", type_="check")
    op.create_check_constraint(
        "ck_assistant_turns_origin_mock", "assistant_turns", "reply_origin = 'mock'"
    )
