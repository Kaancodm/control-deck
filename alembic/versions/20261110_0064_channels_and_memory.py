"""Channel turns, quarantined messages, owner-approved memory, and taskless audit rows.

Every owner chat is a channel. Inbound turns land in ``assistant_messages`` as
``quarantined``. Only an owner approval row (``memory_approvals``) moves a message,
fact, thread topic, or thread link out of its pending state. Conversation audit
rows go into the existing append-only ``events`` table with ``task_id`` NULL.

Downgrade is for test and development databases only. It refuses while any
taskless audit row or any conversation row exists and never deletes them.

Revision ID: 20261110_0064
Revises: 20261108_0063
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261110_0064"
down_revision = "20261108_0063"
branch_labels = None
depends_on = None

_DATA_CLASS = "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')"
_CHANNEL = "IN ('deck_ui', 'slack', 'n8n', 'grok', 'claude', 'claude_code')"
_LABEL = "~ '^[a-z][a-z0-9_]{0,31}$'"
_HEX64 = "~ '^[0-9a-f]{64}$'"
_METHOD = "method IN ('deck_ui', 'live_push', 'import_file', 'owner_typed')"
_EVIDENCE = "evidence_status IN ('bewiesen', 'unbewiesen', 'abgelehnt')"
# Owner decision 2026-11-10: a privacy finding marks the row instead of rejecting it.
_PRIVACY_HOLD = "privacy_hold IS NULL OR privacy_hold IN ('privacy_block', 'blocklist_unavailable')"
_EVENTS_SCOPE = (
    "(task_id IS NOT NULL AND to_status IS NOT NULL) OR "
    "(task_id IS NULL AND to_status IS NULL AND from_status IS NULL "
    "AND (event_type LIKE 'assistant.%' OR event_type LIKE 'memory.%'))"
)
# Downgrade locks assistant_channel_accounts first, then these in live-ingest order
# (link, topic, message, then the rest), and events last.
_CONTENT_TABLES = (
    "assistant_thread_links",
    "assistant_threads",
    "assistant_messages",
    "memory_facts",
    "memory_approvals",
)


def _provenance(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(f"source_channel {_CHANNEL}", name=f"ck_{prefix}_source_channel"),
        sa.CheckConstraint(f"source_account {_LABEL}", name=f"ck_{prefix}_source_account"),
        sa.CheckConstraint(_METHOD, name=f"ck_{prefix}_method"),
        sa.CheckConstraint(_EVIDENCE, name=f"ck_{prefix}_evidence_status"),
        sa.CheckConstraint(
            "char_length(source_ref) BETWEEN 1 AND 300 AND position('://' in source_ref) = 0",
            name=f"ck_{prefix}_source_ref",
        ),
        sa.CheckConstraint(
            f"source_ref_sha256 IS NULL OR source_ref_sha256 {_HEX64}",
            name=f"ck_{prefix}_source_ref_sha256",
        ),
        # "bewiesen" exists only with an owner approval row.
        sa.CheckConstraint(
            "evidence_status <> 'bewiesen' OR approval_id IS NOT NULL",
            name=f"ck_{prefix}_proven_needs_approval",
        ),
        sa.CheckConstraint(_PRIVACY_HOLD, name=f"ck_{prefix}_privacy_hold"),
    ]


def _provenance_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_channel", sa.String(16), nullable=False),
        sa.Column("source_account", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(300), nullable=False),
        sa.Column("source_ref_sha256", sa.String(64), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("evidence_status", sa.String(16), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Immutable content column; cloud context targets never see a held row.
        sa.Column("privacy_hold", sa.String(32), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "assistant_channel_accounts",
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("account_label", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("allowed_data_classes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("channel", "account_label"),
        sa.CheckConstraint(f"channel {_CHANNEL}", name="ck_assistant_channel_accounts_channel"),
        sa.CheckConstraint(f"account_label {_LABEL}", name="ck_assistant_channel_accounts_label"),
        sa.CheckConstraint(
            "cardinality(allowed_data_classes) >= 1 AND allowed_data_classes "
            "<@ ARRAY['local_only', 'cloud_redacted', 'cloud_ok']::text[]",
            name="ck_assistant_channel_accounts_classes",
        ),
        # Decision 7.7: cloud channels never carry local_only content.
        sa.CheckConstraint(
            "channel NOT IN ('slack', 'n8n', 'grok', 'claude') "
            "OR NOT ('local_only' = ANY(allowed_data_classes))",
            name="ck_assistant_channel_accounts_cloud_ceiling",
        ),
        # Decision 7.7: claude_code imports stay local_only until reviewed.
        sa.CheckConstraint(
            "channel <> 'claude_code' OR allowed_data_classes = ARRAY['local_only']::text[]",
            name="ck_assistant_channel_accounts_claude_code_local",
        ),
    )
    op.execute(
        text(
            "INSERT INTO assistant_channel_accounts "
            "(channel, account_label, active, allowed_data_classes) VALUES "
            "('deck_ui', 'owner', true, "
            "ARRAY['local_only', 'cloud_redacted', 'cloud_ok']::text[])"
        )
    )

    op.create_table(
        "memory_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("shown_sha256", sa.String(64), nullable=False),
        sa.Column("approver_ref", sa.String(64), nullable=False),
        # Set only on an owner correction: the approval this row supersedes.
        sa.Column("corrects_approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Target of the composite self-reference below.
        sa.UniqueConstraint("id", "kind", "target_id", name="uq_memory_approvals_identity"),
        # Each approval is corrected at most once, so corrections form one chain.
        sa.UniqueConstraint("corrects_approval_id", name="uq_memory_approvals_corrects"),
        # A correction must name an approval of the same kind and target.
        sa.ForeignKeyConstraint(
            ["corrects_approval_id", "kind", "target_id"],
            ["memory_approvals.id", "memory_approvals.kind", "memory_approvals.target_id"],
            name="fk_memory_approvals_corrects",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "corrects_approval_id IS NULL OR corrects_approval_id <> id",
            name="ck_memory_approvals_corrects_not_self",
        ),
        sa.CheckConstraint(
            "kind IN ('message', 'fact', 'thread_topic', 'thread_link')",
            name="ck_memory_approvals_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_memory_approvals_decision"
        ),
        sa.CheckConstraint(f"shown_sha256 {_HEX64}", name="ck_memory_approvals_shown_sha256"),
        sa.CheckConstraint(f"approver_ref {_HEX64}", name="ck_memory_approvals_approver_ref"),
    )
    # One first decision per target; later rows for the same target are corrections.
    op.create_index(
        "uq_memory_approvals_target",
        "memory_approvals",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text("corrects_approval_id IS NULL"),
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION memory_approvals_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'memory_approvals is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        text(
            "CREATE TRIGGER trg_memory_approvals_append_only "
            "BEFORE UPDATE OR DELETE ON memory_approvals "
            "FOR EACH ROW EXECUTE FUNCTION memory_approvals_append_only()"
        )
    )

    op.create_table(
        "assistant_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("title_origin", sa.String(24), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["approval_id"], ["memory_approvals.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name="ck_assistant_threads_title"
        ),
        sa.CheckConstraint(
            "title_origin IN ('owner', 'assistant_proposed')",
            name="ck_assistant_threads_title_origin",
        ),
        sa.CheckConstraint(_DATA_CLASS, name="ck_assistant_threads_data_class"),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'rejected')", name="ck_assistant_threads_status"
        ),
        sa.CheckConstraint(
            "(status = 'proposed' AND approval_id IS NULL) "
            "OR (status <> 'proposed' AND approval_id IS NOT NULL)",
            name="ck_assistant_threads_decision",
        ),
    )

    op.create_table(
        "assistant_thread_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("account", sa.String(32), nullable=False),
        sa.Column("external_thread_id", sa.String(200), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["thread_id"], ["assistant_threads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["memory_approvals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["channel", "account"],
            [
                "assistant_channel_accounts.channel",
                "assistant_channel_accounts.account_label",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'active', 'rejected')", name="ck_assistant_thread_links_state"
        ),
        sa.CheckConstraint(
            "(state = 'proposed' AND approval_id IS NULL) "
            "OR (state <> 'proposed' AND approval_id IS NOT NULL)",
            name="ck_assistant_thread_links_decision",
        ),
        sa.CheckConstraint(
            "char_length(external_thread_id) BETWEEN 1 AND 200",
            name="ck_assistant_thread_links_external_thread",
        ),
        sa.CheckConstraint(
            "position('://' in external_thread_id) = 0",
            name="ck_assistant_thread_links_external_scheme",
        ),
    )
    # One open or accepted link per external thread; rejected rows stay as history.
    op.create_index(
        "uq_assistant_thread_links_external",
        "assistant_thread_links",
        ["channel", "account", "external_thread_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'rejected'"),
    )
    op.create_index("ix_assistant_thread_links_thread_id", "assistant_thread_links", ["thread_id"])

    op.create_table(
        "assistant_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_thread_id", sa.String(200), nullable=False),
        sa.Column("external_message_id", sa.String(200), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reply_for_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("in_reply_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_provenance_columns(),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq", name="uq_assistant_messages_seq"),
        sa.UniqueConstraint(
            "source_channel",
            "source_account",
            "external_message_id",
            name="uq_assistant_messages_external_id",
        ),
        sa.UniqueConstraint("reply_for_message_id", name="uq_assistant_messages_reply_for"),
        sa.ForeignKeyConstraint(["thread_id"], ["assistant_threads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reply_for_message_id"], ["assistant_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["in_reply_to_message_id"], ["assistant_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["memory_approvals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_channel", "source_account"],
            [
                "assistant_channel_accounts.channel",
                "assistant_channel_accounts.account_label",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("role IN ('owner', 'assistant')", name="ck_assistant_messages_role"),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 16000", name="ck_assistant_messages_text_len"
        ),
        sa.CheckConstraint(
            "text_sha256 = encode(sha256(convert_to(text, 'UTF8')), 'hex')",
            name="ck_assistant_messages_text_sha256",
        ),
        sa.CheckConstraint(_DATA_CLASS, name="ck_assistant_messages_data_class"),
        sa.CheckConstraint(
            "state IN ('quarantined', 'approved', 'rejected')", name="ck_assistant_messages_state"
        ),
        sa.CheckConstraint(
            "(state = 'quarantined' AND approval_id IS NULL "
            "AND evidence_status = 'unbewiesen') "
            "OR (state = 'approved' AND approval_id IS NOT NULL "
            "AND evidence_status IN ('bewiesen', 'unbewiesen')) "
            "OR (state = 'rejected' AND approval_id IS NOT NULL "
            "AND evidence_status = 'abgelehnt')",
            name="ck_assistant_messages_decision",
        ),
        # Exactly one Deck reply per inbound message, only where the Deck answers.
        sa.CheckConstraint(
            "reply_for_message_id IS NULL "
            "OR (role = 'assistant' AND source_channel IN ('deck_ui', 'slack'))",
            name="ck_assistant_messages_reply_channel",
        ),
        sa.CheckConstraint(
            "reply_for_message_id IS NULL OR reply_for_message_id <> id",
            name="ck_assistant_messages_reply_not_self",
        ),
        sa.CheckConstraint(
            "position('://' in external_thread_id) = 0 AND "
            "(external_message_id IS NULL OR position('://' in external_message_id) = 0)",
            name="ck_assistant_messages_external_scheme",
        ),
        *_provenance("assistant_messages"),
    )
    # Fallback dedup when the source has no message id.
    op.create_index(
        "uq_assistant_messages_fallback",
        "assistant_messages",
        [
            "source_channel",
            "source_account",
            "external_thread_id",
            "role",
            "text_sha256",
            "source_date",
        ],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NULL"),
    )
    op.create_index("ix_assistant_messages_thread_seq", "assistant_messages", ["thread_id", "seq"])
    op.create_index(
        "ix_assistant_messages_quarantine",
        "assistant_messages",
        ["seq"],
        postgresql_where=sa.text("state = 'quarantined'"),
    )

    op.create_table(
        "memory_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_sentence", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(200), nullable=False),
        sa.Column("object", sa.String(1000), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_class", sa.String(16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        *_provenance_columns(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["assistant_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["superseded_by"], ["memory_facts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["memory_approvals.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "char_length(original_sentence) BETWEEN 1 AND 4000",
            name="ck_memory_facts_original_len",
        ),
        sa.CheckConstraint(
            "char_length(subject) >= 1 AND char_length(predicate) >= 1 "
            "AND char_length(object) >= 1",
            name="ck_memory_facts_triple",
        ),
        sa.CheckConstraint(_DATA_CLASS, name="ck_memory_facts_data_class"),
        sa.CheckConstraint(
            "status IN ('proposed', 'approved', 'rejected')", name="ck_memory_facts_status"
        ),
        sa.CheckConstraint(
            "(status = 'proposed' AND approval_id IS NULL AND evidence_status = 'unbewiesen') "
            "OR (status = 'approved' AND approval_id IS NOT NULL "
            "AND evidence_status IN ('bewiesen', 'unbewiesen')) "
            "OR (status = 'rejected' AND approval_id IS NOT NULL "
            "AND evidence_status = 'abgelehnt')",
            name="ck_memory_facts_decision",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_memory_facts_validity",
        ),
        sa.CheckConstraint(
            "superseded_by IS NULL OR superseded_by <> id", name="ck_memory_facts_not_self"
        ),
        *_provenance("memory_facts"),
    )
    op.create_index("ix_memory_facts_source_message", "memory_facts", ["source_message_id"])
    op.create_index(
        "ix_memory_facts_approved",
        "memory_facts",
        ["data_class", "created_at"],
        postgresql_where=sa.text("status = 'approved' AND superseded_by IS NULL"),
    )

    # Decision 7.6: conversation audit lives in the existing append-only events table.
    op.alter_column("events", "task_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column(
        "events",
        "to_status",
        existing_type=postgresql.ENUM(name="task_state", create_type=False),
        nullable=True,
    )
    op.create_check_constraint("ck_events_task_or_conversation", "events", _EVENTS_SCOPE)
    op.create_index(
        "ix_events_conversation_created_at",
        "events",
        ["created_at"],
        postgresql_where=sa.text("task_id IS NULL"),
    )
    _create_content_guards()


def _create_content_guards() -> None:
    """Freeze shown content, freeze the decision after the pending state, forbid deletes.

    Exactly one decision update leaves ``quarantined``/``proposed``. Afterwards the
    decision columns change only through an owner correction: the new
    ``approval_id`` must name a ``memory_approvals`` row of the same kind and
    target whose ``corrects_approval_id`` is the old ``approval_id`` and whose
    decision matches the new state, and the new state must be a decided state.
    Content columns, ``data_class`` and ``privacy_hold`` never change. A message
    ``thread_id`` may only move from NULL; a link ``thread_id`` is fixed after
    the first decision.

    ``TRUNCATE`` does not fire these row triggers, so test cleanup can still
    empty the tables. ``memory_approvals`` keeps its own append-only trigger.
    """
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION memory_is_correction(
                p_kind text, p_target uuid, p_old uuid, p_new uuid, p_decision text
            )
            RETURNS boolean AS $$
                SELECT p_old IS NOT NULL AND p_new IS NOT NULL AND p_new <> p_old
                   AND EXISTS (
                       SELECT 1 FROM memory_approvals AS a
                       WHERE a.id = p_new
                         AND a.corrects_approval_id = p_old
                         AND a.kind = p_kind
                         AND a.target_id = p_target
                         AND a.decision = p_decision
                   );
            $$ LANGUAGE sql STABLE;
            """
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION memory_content_immutable()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'memory_content_immutable';
                END IF;
                IF TG_TABLE_NAME = 'assistant_messages' THEN
                    IF NEW.text IS DISTINCT FROM OLD.text
                       OR NEW.text_sha256 IS DISTINCT FROM OLD.text_sha256
                       OR NEW.data_class IS DISTINCT FROM OLD.data_class
                       OR NEW.privacy_hold IS DISTINCT FROM OLD.privacy_hold
                       OR NEW.role IS DISTINCT FROM OLD.role
                       OR NEW.external_thread_id IS DISTINCT FROM OLD.external_thread_id
                       OR NEW.external_message_id IS DISTINCT FROM OLD.external_message_id
                       OR NEW.source_date IS DISTINCT FROM OLD.source_date
                       OR NEW.source_channel IS DISTINCT FROM OLD.source_channel
                       OR NEW.source_account IS DISTINCT FROM OLD.source_account
                       OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
                       OR NEW.source_ref_sha256 IS DISTINCT FROM OLD.source_ref_sha256
                       OR NEW.method IS DISTINCT FROM OLD.method
                       OR (OLD.thread_id IS NOT NULL
                           AND NEW.thread_id IS DISTINCT FROM OLD.thread_id)
                       OR (OLD.state <> 'quarantined'
                           AND (NEW.state IS DISTINCT FROM OLD.state
                                OR NEW.evidence_status IS DISTINCT FROM OLD.evidence_status
                                OR NEW.approval_id IS DISTINCT FROM OLD.approval_id)
                           AND NOT (NEW.state IN ('approved', 'rejected')
                                    AND memory_is_correction(
                                        'message', OLD.id, OLD.approval_id, NEW.approval_id,
                                        CASE WHEN NEW.state = 'rejected'
                                             THEN 'rejected' ELSE 'approved' END)))
                    THEN
                        RAISE EXCEPTION 'memory_content_immutable';
                    END IF;
                ELSIF TG_TABLE_NAME = 'memory_facts' THEN
                    IF NEW.original_sentence IS DISTINCT FROM OLD.original_sentence
                       OR NEW.subject IS DISTINCT FROM OLD.subject
                       OR NEW.predicate IS DISTINCT FROM OLD.predicate
                       OR NEW.object IS DISTINCT FROM OLD.object
                       OR NEW.data_class IS DISTINCT FROM OLD.data_class
                       OR NEW.privacy_hold IS DISTINCT FROM OLD.privacy_hold
                       OR NEW.source_message_id IS DISTINCT FROM OLD.source_message_id
                       OR NEW.source_date IS DISTINCT FROM OLD.source_date
                       OR NEW.source_channel IS DISTINCT FROM OLD.source_channel
                       OR NEW.source_account IS DISTINCT FROM OLD.source_account
                       OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
                       OR NEW.source_ref_sha256 IS DISTINCT FROM OLD.source_ref_sha256
                       OR NEW.method IS DISTINCT FROM OLD.method
                       OR (OLD.status <> 'proposed'
                           AND NEW.valid_from IS DISTINCT FROM OLD.valid_from)
                       OR (OLD.status <> 'proposed'
                           AND (NEW.status IS DISTINCT FROM OLD.status
                                OR NEW.evidence_status IS DISTINCT FROM OLD.evidence_status
                                OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
                                OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
                                OR NEW.superseded_by IS DISTINCT FROM OLD.superseded_by)
                           AND NOT (NEW.status IN ('approved', 'rejected')
                                    AND memory_is_correction(
                                        'fact', OLD.id, OLD.approval_id, NEW.approval_id,
                                        CASE WHEN NEW.status = 'rejected'
                                             THEN 'rejected' ELSE 'approved' END)))
                    THEN
                        RAISE EXCEPTION 'memory_content_immutable';
                    END IF;
                ELSIF TG_TABLE_NAME = 'assistant_threads' THEN
                    IF NEW.data_class IS DISTINCT FROM OLD.data_class
                       OR NEW.title IS DISTINCT FROM OLD.title
                       OR (OLD.status <> 'proposed'
                           AND (NEW.status IS DISTINCT FROM OLD.status
                                OR NEW.approval_id IS DISTINCT FROM OLD.approval_id)
                           AND NOT (NEW.status IN ('active', 'rejected')
                                    AND memory_is_correction(
                                        'thread_topic', OLD.id, OLD.approval_id,
                                        NEW.approval_id,
                                        CASE WHEN NEW.status = 'rejected'
                                             THEN 'rejected' ELSE 'approved' END)))
                    THEN
                        RAISE EXCEPTION 'memory_content_immutable';
                    END IF;
                ELSIF TG_TABLE_NAME = 'assistant_thread_links' THEN
                    IF NEW.channel IS DISTINCT FROM OLD.channel
                       OR NEW.account IS DISTINCT FROM OLD.account
                       OR NEW.external_thread_id IS DISTINCT FROM OLD.external_thread_id
                       OR (OLD.state <> 'proposed'
                           AND NEW.thread_id IS DISTINCT FROM OLD.thread_id)
                       OR (OLD.state <> 'proposed'
                           AND (NEW.state IS DISTINCT FROM OLD.state
                                OR NEW.approval_id IS DISTINCT FROM OLD.approval_id)
                           AND NOT (NEW.state IN ('active', 'rejected')
                                    AND memory_is_correction(
                                        'thread_link', OLD.id, OLD.approval_id,
                                        NEW.approval_id,
                                        CASE WHEN NEW.state = 'rejected'
                                             THEN 'rejected' ELSE 'approved' END)))
                    THEN
                        RAISE EXCEPTION 'memory_content_immutable';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    for table in (
        "assistant_messages",
        "memory_facts",
        "assistant_threads",
        "assistant_thread_links",
    ):
        op.execute(
            text(
                f"CREATE TRIGGER trg_{table}_content "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION memory_content_immutable()"
            )
        )


def _drop_content_guards() -> None:
    for table in (
        "assistant_messages",
        "memory_facts",
        "assistant_threads",
        "assistant_thread_links",
    ):
        op.execute(text(f"DROP TRIGGER IF EXISTS trg_{table}_content ON {table}"))
    op.execute(text("DROP FUNCTION IF EXISTS memory_content_immutable()"))
    op.execute(text("DROP FUNCTION IF EXISTS memory_is_correction(text, uuid, uuid, uuid, text)"))


def downgrade() -> None:
    bind = op.get_bind()
    # lock_timeout bounds a one-way wait; Postgres deadlock detection aborts a
    # cycle. Either way the single migration transaction rolls back cleanly.
    bind.execute(text("SET LOCAL lock_timeout = '10s'"))
    # ACCESS EXCLUSIVE up front: DROP/ALTER need it anyway, and taking it here in
    # ingest order (accounts, content tables, events last) makes the first
    # conflicting lock come before any count. Writers that already hold a row
    # lock make the downgrade wait or abort; nothing is half-dropped.
    bind.execute(text("LOCK TABLE assistant_channel_accounts IN ACCESS EXCLUSIVE MODE"))
    for table in _CONTENT_TABLES:
        bind.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
    bind.execute(text("LOCK TABLE events IN ACCESS EXCLUSIVE MODE"))
    taskless = bind.execute(text("SELECT count(*) FROM events WHERE task_id IS NULL")).scalar_one()
    if taskless:
        raise RuntimeError(
            "events_taskless_rows: conversation audit rows exist with task_id NULL; "
            "refusing to restore NOT NULL. Rows are never deleted by this migration."
        )
    for table in _CONTENT_TABLES:
        remaining = bind.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        if remaining:
            raise RuntimeError(
                f"conversation_rows: {table} is not empty; refusing to drop owner memory."
            )
    _drop_content_guards()
    op.drop_index("ix_events_conversation_created_at", table_name="events")
    op.drop_constraint("ck_events_task_or_conversation", "events", type_="check")
    op.alter_column(
        "events",
        "to_status",
        existing_type=postgresql.ENUM(name="task_state", create_type=False),
        nullable=False,
    )
    op.alter_column(
        "events", "task_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    op.drop_index("ix_memory_facts_approved", table_name="memory_facts")
    op.drop_index("ix_memory_facts_source_message", table_name="memory_facts")
    op.drop_table("memory_facts")
    op.drop_index("ix_assistant_messages_quarantine", table_name="assistant_messages")
    op.drop_index("ix_assistant_messages_thread_seq", table_name="assistant_messages")
    op.drop_index("uq_assistant_messages_fallback", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_thread_links_thread_id", table_name="assistant_thread_links")
    op.drop_index("uq_assistant_thread_links_external", table_name="assistant_thread_links")
    op.drop_table("assistant_thread_links")
    op.drop_table("assistant_threads")
    op.execute(text("DROP TRIGGER trg_memory_approvals_append_only ON memory_approvals"))
    op.execute(text("DROP FUNCTION memory_approvals_append_only()"))
    op.drop_index("uq_memory_approvals_target", table_name="memory_approvals")
    op.drop_table("memory_approvals")
    op.drop_table("assistant_channel_accounts")
