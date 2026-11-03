"""SQLAlchemy ORM models for tasks, results, events, and leases."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.schemas import DataClass
from app.db.base import Base
from app.db.state_machine import TaskState

_task_state_enum = Enum(
    TaskState,
    name="task_state",
    native_enum=True,
    create_constraint=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

_data_class_enum = Enum(
    DataClass,
    name="data_class",
    native_enum=True,
    create_constraint=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Task(Base):
    """Persisted task row; immutable ``task.v1`` payload lives in ``payload``."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[TaskState] = mapped_column(
        _task_state_enum,
        nullable=False,
        default=TaskState.QUEUED,
        server_default=TaskState.QUEUED.value,
    )
    data_class: Mapped[DataClass] = mapped_column(
        _data_class_enum,
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    lane: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    results: Mapped[list[Result]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(back_populates="task", cascade="all, delete-orphan")
    lease: Mapped[Lease | None] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_data_class", "data_class"),
        CheckConstraint(
            "payload->>'schema_version' = 'task.v1'",
            name="ck_tasks_payload_schema_version",
        ),
        CheckConstraint(
            "payload->>'data_class' IS NOT NULL AND (payload->>'data_class') = data_class::text",
            name="ck_tasks_payload_data_class",
        ),
        CheckConstraint(
            "payload->>'task_id' IS NOT NULL AND (payload->>'task_id')::uuid = id",
            name="ck_tasks_payload_task_id",
        ),
    )


class Result(Base):
    """Append-only ``result.v1`` snapshots per attempt."""

    __tablename__ = "results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="results")

    __table_args__ = (
        Index("ix_results_task_id_attempt", "task_id", "attempt", unique=True),
        CheckConstraint("attempt >= 1", name="ck_results_attempt_min"),
        CheckConstraint(
            "payload->>'schema_version' = 'result.v1'",
            name="ck_results_payload_schema_version",
        ),
        CheckConstraint(
            "payload->>'task_id' IS NOT NULL AND (payload->>'task_id')::uuid = task_id",
            name="ck_results_payload_task_id",
        ),
        CheckConstraint(
            "payload->>'attempt' IS NOT NULL AND (payload->>'attempt')::int = attempt",
            name="ck_results_payload_attempt",
        ),
    )


class Event(Base):
    """Append-only audit log for task lifecycle transitions."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_status: Mapped[TaskState | None] = mapped_column(_task_state_enum, nullable=True)
    to_status: Mapped[TaskState] = mapped_column(_task_state_enum, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="events")

    __table_args__ = (Index("ix_events_task_id_created_at", "task_id", "created_at"),)


class Lease(Base):
    """Exclusive pull lease for a queued task (one active row per task)."""

    __tablename__ = "leases"

    lease_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    holder: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="lease")

    __table_args__ = (Index("ix_leases_expires_at", "expires_at"),)
