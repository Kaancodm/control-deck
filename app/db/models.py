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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.assistant import records as _assistant_records  # noqa: F401 — register assistant metadata
from app.core.schemas import DataClass
from app.db.base import Base
from app.db.state_machine import TaskState
from app.kb import state as _knowledge_state  # noqa: F401 — register local snapshot metadata
from app.memory import models as _memory_models  # noqa: F401 — register conversation memory
from app.operations import models as _operation_models  # noqa: F401

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
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retry_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    channel_receipt: Mapped[ChannelReceipt | None] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )
    routine_wakes: Mapped[list[RoutineOutbox]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_data_class", "data_class"),
        Index(
            "ix_tasks_queued_next_attempt",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_tasks_active_deadline",
            "deadline_at",
            "id",
            postgresql_where=text(
                "deadline_at IS NOT NULL AND status NOT IN "
                "('succeeded', 'done', 'expired', 'failed', 'blocked', 'cancelled')"
            ),
        ),
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
    """Append-only ``result.v1`` snapshots with separately trusted provenance."""

    __tablename__ = "results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_profile: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="simulation"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="results")

    __table_args__ = (
        Index("ix_results_task_id_attempt", "task_id", "attempt", unique=True),
        CheckConstraint("attempt >= 1", name="ck_results_attempt_min"),
        CheckConstraint(
            "execution_profile IN ('simulation', 'live', 'remote', 'unknown')",
            name="ck_results_execution_profile",
        ),
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
    """Append-only audit log for task transitions and taskless conversation audit.

    Task rows always carry ``task_id`` and ``to_status``. Conversation and memory
    rows (``assistant.*`` / ``memory.*``) carry neither; a CHECK enforces the split.
    Task views must filter ``task_id IS NOT NULL``.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_status: Mapped[TaskState | None] = mapped_column(_task_state_enum, nullable=True)
    to_status: Mapped[TaskState | None] = mapped_column(_task_state_enum, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task | None] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_events_task_id_created_at", "task_id", "created_at"),
        Index(
            "ix_events_conversation_created_at",
            "created_at",
            postgresql_where=text("task_id IS NULL"),
        ),
        CheckConstraint(
            "(task_id IS NOT NULL AND to_status IS NOT NULL) OR "
            "(task_id IS NULL AND to_status IS NULL AND from_status IS NULL "
            "AND (event_type LIKE 'assistant.%' OR event_type LIKE 'memory.%'))",
            name="ck_events_task_or_conversation",
        ),
    )


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


class ChannelReceipt(Base):
    """One durable acceptance record per task. It is not ``task.start``."""

    __tablename__ = "channel_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[Task] = relationship(back_populates="channel_receipt")

    __table_args__ = (
        Index(
            "ix_channel_receipts_deadline",
            "deadline_at",
            postgresql_where=text("status = 'accepted'"),
        ),
        CheckConstraint(
            "status IN ('accepted', 'delivered', 'deadline_elapsed')",
            name="ck_channel_receipts_status",
        ),
        CheckConstraint(
            "transport = 'missing' OR transport ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="ck_channel_receipts_transport",
        ),
        CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$'",
            name="ck_channel_receipts_idempotency_key",
        ),
        CheckConstraint(
            "deadline_at = accepted_at + interval '60 seconds'",
            name="ck_channel_receipts_deadline",
        ),
        CheckConstraint(
            "(status = 'delivered' AND transport <> 'missing' AND delivered_at IS NOT NULL) "
            "OR (status <> 'delivered' AND delivered_at IS NULL)",
            name="ck_channel_receipts_delivery",
        ),
    )


class RoutineOutbox(Base):
    """One completion wake. It is not a task status and not a delivery receipt."""

    __tablename__ = "routine_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_status: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task: Mapped[Task] = relationship(back_populates="routine_wakes")

    __table_args__ = (
        UniqueConstraint("task_id", "event", name="uq_routine_outbox_task_event"),
        Index(
            "ix_routine_outbox_dispatch",
            "created_at",
            postgresql_where=text("state IN ('pending', 'start_failed')"),
        ),
        CheckConstraint("event = 'task.completed'", name="ck_routine_outbox_event"),
        CheckConstraint("schema_version = 'deck.wake.v1'", name="ck_routine_outbox_version"),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_routine_outbox_payload_hash",
        ),
        CheckConstraint("task_status IN ('review', 'done')", name="ck_routine_outbox_task_status"),
        CheckConstraint(
            "state IN ('pending', 'sending', 'accepted', 'start_failed', "
            "'unknown_start', 'acknowledged')",
            name="ck_routine_outbox_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_routine_outbox_attempts",
        ),
        CheckConstraint(
            "(state IN ('accepted', 'acknowledged') AND accepted_at IS NOT NULL) "
            "OR (state NOT IN ('accepted', 'acknowledged') AND accepted_at IS NULL)",
            name="ck_routine_outbox_accepted",
        ),
        CheckConstraint(
            "(state = 'acknowledged' AND acknowledged_at IS NOT NULL) "
            "OR (state <> 'acknowledged' AND acknowledged_at IS NULL)",
            name="ck_routine_outbox_acknowledged",
        ),
    )


class RedactionMap(Base):
    """Local, immutable task-scoped originals; never included in API serializers."""

    __tablename__ = "redaction_maps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    placeholder: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original: Mapped[str] = mapped_column(Text, nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    first_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "placeholder", name="uq_redaction_maps_task_placeholder"),
        UniqueConstraint("task_id", "original_sha256", name="uq_redaction_maps_task_original_hash"),
        Index("ix_redaction_maps_task_id", "task_id"),
        CheckConstraint(
            "kind IN ('EMAIL', 'PHONE', 'IBAN', 'AHV', 'IP', 'TERM')",
            name="ck_redaction_maps_kind",
        ),
        CheckConstraint("first_attempt >= 1", name="ck_redaction_maps_first_attempt"),
        CheckConstraint("original <> ''", name="ck_redaction_maps_original_not_empty"),
        CheckConstraint(
            "original_sha256 = encode(sha256(convert_to(original, 'UTF8')), 'hex')",
            name="ck_redaction_maps_original_hash",
        ),
        CheckConstraint(
            r"placeholder ~ ('^\[\[CD_' || replace(task_id::text, '-', '') || '_' || "
            r"kind || '_[1-9][0-9]{0,9}\]\]$')",
            name="ck_redaction_maps_placeholder_scope",
        ),
    )


class Setting(Base):
    """Local runner controls; values are validated before use or mutation."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LaneStatus(Base):
    """Last observed lane health, with safe reason codes and a retained success time."""

    __tablename__ = "lane_status"

    lane: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    simulated_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    remote_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    unknown_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    probe_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    completed_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    consecutive_failures: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("lane IN ('ollama', 'claude', 'codex')", name="ck_lane_status_lane"),
        CheckConstraint(
            "status IN ('ok', 'unavailable', 'auth_required', 'paused')",
            name="ck_lane_status_status",
        ),
        CheckConstraint("reason ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_lane_status_reason"),
        CheckConstraint("simulated_attempts >= 0", name="ck_lane_status_simulated_attempts"),
        CheckConstraint("remote_attempts >= 0", name="ck_lane_status_remote_attempts"),
        CheckConstraint("unknown_attempts >= 0", name="ck_lane_status_unknown_attempts"),
        CheckConstraint("probe_generation >= 0", name="ck_lane_status_probe_generation"),
        CheckConstraint(
            "completed_attempts >= 0 AND consecutive_failures >= 0 "
            "AND consecutive_failures <= completed_attempts + simulated_attempts "
            "+ remote_attempts + unknown_attempts",
            name="ck_lane_status_counters",
        ),
        CheckConstraint(
            "last_error_code IN ('worker_unavailable', 'auth_required', 'rate_limited', "
            "'timeout', 'policy_blocked', 'acceptance_failed', 'internal')",
            name="ck_lane_status_error_code",
        ),
    )
