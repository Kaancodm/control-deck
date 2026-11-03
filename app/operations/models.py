"""Append-only maintenance audit, separate from task lifecycle events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OperationEvent(Base):
    """A durable phase observation; completion is written only after the work succeeds."""

    __tablename__ = "operations_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('backup', 'restore', 'retention', 'update')",
            name="ck_operations_events_type",
        ),
        CheckConstraint(
            "phase IN ('started', 'artifacts_removed', 'completed', 'failed')",
            name="ck_operations_events_phase",
        ),
        Index("ix_operations_events_operation_id", "operation_id", "id"),
        Index("ix_operations_events_created_at", "created_at", "id"),
    )
