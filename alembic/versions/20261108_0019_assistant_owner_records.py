"""Owner profile, card history, optional dialogue, and order idempotency keys.

These tables are not a task queue. Orders still go through ``tasks``.

Revision ID: 20261108_0019
Revises: 20261104_0018
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261108_0019"
down_revision = "20261104_0018"
branch_labels = None
depends_on = None

_DATA_CLASS = "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')"
_CATEGORY = "category IN ('frage', 'code', 'checkliste', 'planung')"


def upgrade() -> None:
    op.create_table(
        "assistant_profile_versions",
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_assistant_profile_version"),
        sa.CheckConstraint("char_length(notes) <= 4000", name="ck_assistant_profile_notes_len"),
    )
    op.create_table(
        "assistant_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("placeholders", postgresql.JSONB(), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_assistant_cards_version"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_assistant_cards_name"),
        sa.CheckConstraint(
            "char_length(template) BETWEEN 1 AND 16384", name="ck_assistant_cards_template"
        ),
        sa.CheckConstraint(_CATEGORY, name="ck_assistant_cards_category"),
        sa.CheckConstraint(_DATA_CLASS, name="ck_assistant_cards_data_class"),
    )
    op.create_table(
        "assistant_card_versions",
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("placeholders", postgresql.JSONB(), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["card_id"], ["assistant_cards.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("card_id", "version"),
        sa.CheckConstraint("version >= 1", name="ck_assistant_card_versions_version"),
        sa.CheckConstraint(_CATEGORY, name="ck_assistant_card_versions_category"),
        sa.CheckConstraint(_DATA_CLASS, name="ck_assistant_card_versions_data_class"),
    )
    op.create_index("ix_assistant_card_versions_card_id", "assistant_card_versions", ["card_id"])
    op.create_table(
        "assistant_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("owner_text", sa.Text(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("reply_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(_DATA_CLASS, name="ck_assistant_turns_data_class"),
        sa.CheckConstraint("reply_origin = 'mock'", name="ck_assistant_turns_origin_mock"),
        sa.CheckConstraint(
            "char_length(owner_text) BETWEEN 1 AND 4000", name="ck_assistant_turns_owner_len"
        ),
        sa.CheckConstraint(
            "char_length(reply_text) BETWEEN 1 AND 4000", name="ck_assistant_turns_reply_len"
        ),
    )
    op.create_index("ix_assistant_turns_created_at", "assistant_turns", ["created_at"])
    op.create_table(
        "assistant_order_keys",
        sa.Column("idempotency_key", sa.String(36), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", name="uq_assistant_order_keys_task"),
        sa.CheckConstraint("char_length(idempotency_key) = 36", name="ck_assistant_order_keys_len"),
    )


def downgrade() -> None:
    op.drop_table("assistant_order_keys")
    op.drop_index("ix_assistant_turns_created_at", table_name="assistant_turns")
    op.drop_table("assistant_turns")
    op.drop_index("ix_assistant_card_versions_card_id", table_name="assistant_card_versions")
    op.drop_table("assistant_card_versions")
    op.drop_table("assistant_cards")
    op.drop_table("assistant_profile_versions")
