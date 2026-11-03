"""Host-only context snapshots and durable, idempotent export intents."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContextSnapshot(Base):
    """Exact local bytes selected before approval; retention follows the task."""

    __tablename__ = "task_context_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data_class: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "position", name="uq_context_snapshot_position"),
        UniqueConstraint("task_id", "reference", name="uq_context_snapshot_reference"),
        CheckConstraint("position BETWEEN 0 AND 15", name="ck_context_snapshot_position"),
        CheckConstraint(
            "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')",
            name="ck_context_snapshot_class",
        ),
        CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 25024", name="ck_context_snapshot_size"
        ),
        CheckConstraint(
            "content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
            name="ck_context_snapshot_hash",
        ),
    )


class KnowledgeExport(Base):
    """A durable owner decision resumed safely after a cross-database failure."""

    __tablename__ = "knowledge_exports"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("results.id", ondelete="RESTRICT"), nullable=False
    )
    data_class: Mapped[str] = mapped_column(String(32), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')",
            name="ck_knowledge_export_class",
        ),
        CheckConstraint("state IN ('pending', 'completed')", name="ck_knowledge_export_state"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="ck_knowledge_export_hash"),
    )
