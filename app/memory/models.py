"""Channel accounts, threads, quarantined messages, facts, and owner approvals.

These rows are conversation memory, not a task queue. They never reference ``tasks``.
Write paths live in ``app.memory.ingest`` and ``app.memory.approval``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ACCOUNT_FK = (
    "assistant_channel_accounts.channel",
    "assistant_channel_accounts.account_label",
)


class ChannelAccount(Base):
    """Allowlist entry. Missing or inactive rows reject every turn (fail-closed)."""

    __tablename__ = "assistant_channel_accounts"

    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_label: Mapped[str] = mapped_column(String(32), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allowed_data_classes: Mapped[list[str]] = mapped_column(ARRAY(Text()), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class MemoryApproval(Base):
    """Append-only owner decision bound to an approver reference and the shown bytes."""

    __tablename__ = "memory_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    shown_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # sha256(domain prefix + ui session token hash). Not the token hash, and not a
    # foreign key: sessions are pruned, and the hash must not be a lookup key.
    approver_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (UniqueConstraint("kind", "target_id", name="uq_memory_approvals_target"),)


class AssistantThread(Base):
    """A topic thread. Assistant-proposed titles stay ``proposed`` until the owner clicks."""

    __tablename__ = "assistant_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    title_origin: Mapped[str] = mapped_column(String(24), nullable=False)
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_approvals.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class ThreadLink(Base):
    """Maps one external (channel, account, thread) to a topic thread once accepted."""

    __tablename__ = "assistant_thread_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_threads.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_approvals.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        ForeignKeyConstraint(["channel", "account"], list(_ACCOUNT_FK), ondelete="RESTRICT"),
        Index(
            "uq_assistant_thread_links_external",
            "channel",
            "account",
            "external_thread_id",
            unique=True,
            postgresql_where=sql_text("state <> 'rejected'"),
        ),
        Index("ix_assistant_thread_links_thread_id", "thread_id"),
    )


class AssistantMessage(Base):
    """One message per row. New rows are ``quarantined`` and never enter model context."""

    __tablename__ = "assistant_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_threads.id", ondelete="RESTRICT"), nullable=True
    )
    external_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reply_for_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_messages.id", ondelete="RESTRICT"), nullable=True
    )
    in_reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_messages.id", ondelete="RESTRICT"), nullable=True
    )
    source_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    source_account: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    source_ref_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_approvals.id", ondelete="RESTRICT"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("seq", name="uq_assistant_messages_seq"),
        UniqueConstraint(
            "source_channel",
            "source_account",
            "external_message_id",
            name="uq_assistant_messages_external_id",
        ),
        UniqueConstraint("reply_for_message_id", name="uq_assistant_messages_reply_for"),
        ForeignKeyConstraint(
            ["source_channel", "source_account"], list(_ACCOUNT_FK), ondelete="RESTRICT"
        ),
        Index(
            "uq_assistant_messages_fallback",
            "source_channel",
            "source_account",
            "external_thread_id",
            "role",
            "text_sha256",
            "source_date",
            unique=True,
            postgresql_where=sql_text("external_message_id IS NULL"),
        ),
        Index("ix_assistant_messages_thread_seq", "thread_id", "seq"),
        Index(
            "ix_assistant_messages_quarantine",
            "seq",
            postgresql_where=sql_text("state = 'quarantined'"),
        ),
    )


class MemoryFact(Base):
    """A proposed or approved statement with canonical English triple and provenance."""

    __tablename__ = "memory_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_sentence: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    predicate: Mapped[str] = mapped_column(String(200), nullable=False)
    object: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_messages.id", ondelete="RESTRICT"), nullable=False
    )
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_facts.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    source_account: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    source_ref_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_approvals.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        Index("ix_memory_facts_source_message", "source_message_id"),
        Index(
            "ix_memory_facts_approved",
            "data_class",
            "created_at",
            postgresql_where=sql_text("status = 'approved' AND superseded_by IS NULL"),
        ),
    )
