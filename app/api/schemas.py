"""OpenAPI response and request models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.core.retries import RetryState
from app.core.schemas import ErrorCode, TaskKind, Worker
from app.routing.router import RoutingPlan
from app.workers.contracts import ControlAction


class EventOut(BaseModel):
    id: int
    event_type: str
    from_status: str | None
    to_status: str
    detail: dict[str, Any] | None
    created_at: datetime


class ResultOut(BaseModel):
    id: UUID
    attempt: int
    payload: dict[str, Any]
    created_at: datetime


class TaskSummaryOut(BaseModel):
    id: UUID
    status: str
    data_class: str
    attempt: int
    requires_approval: bool
    approved_at: datetime | None
    deadline_at: datetime | None
    lane: Worker | None = None
    next_attempt_at: datetime | None = None
    retry_state: RetryState | None = None
    created_at: datetime
    updated_at: datetime


class TaskDetailOut(TaskSummaryOut):
    payload: dict[str, Any]
    events: list[EventOut]
    results: list[ResultOut]


class TaskListOut(BaseModel):
    items: list[TaskSummaryOut]
    total: int
    limit: int
    offset: int


class TaskFailureOut(TaskSummaryOut):
    last_error_code: ErrorCode | None


class TaskFailureListOut(BaseModel):
    items: list[TaskFailureOut]
    total: int
    limit: int
    offset: int


class TaskCreateOut(BaseModel):
    task: TaskDetailOut


class TaskReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: StrictBool
    reason: str | None = Field(default=None, max_length=2000)


class LeaseOut(BaseModel):
    lease_id: UUID
    task_id: UUID
    expires_at: datetime
    heartbeat_at: datetime
    holder: str | None


class WorkerTaskOut(BaseModel):
    """Execution metadata only; original tasks and prior results stay on the host."""

    id: UUID
    status: Literal["leased"] = "leased"
    attempt: int
    kind: TaskKind
    worker: Worker
    model: str | None
    timeout_s: int
    deadline_at: datetime | None


class WorkerLeaseResponse(BaseModel):
    task: WorkerTaskOut
    lease: LeaseOut
    payload_text: str
    payload_sha256: str
    redaction_applied: bool
    redaction_placeholders: int
    artifact_path: str


class WorkerStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: UUID
    task_id: UUID
    attempt: int = Field(ge=1)
    worker: Worker


class WorkerHeartbeatRequest(WorkerStartRequest):
    extend_seconds: int | None = Field(default=None, ge=30, le=7200)


class WorkerHeartbeatResponse(BaseModel):
    action: ControlAction
    expires_at: datetime | None = None
    heartbeat_at: datetime | None = None


class WorkerResultRequest(BaseModel):
    lease_id: UUID
    result: dict[str, Any]


class WorkerResultResponse(BaseModel):
    task: TaskSummaryOut


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class DryRunFindingOut(BaseModel):
    kind: str
    rule: str
    source: str
    start: int
    end: int


class LaneBudgetOut(BaseModel):
    used: int
    limit: int | None
    remaining: int | None


class TaskDryRunOut(BaseModel):
    status: Literal["ready", "blocked", "deferred"]
    reason: str | None
    plan: RoutingPlan
    gate_status: Literal["clear", "blocked"]
    findings: list[DryRunFindingOut]
    budgets: dict[Worker, LaneBudgetOut]
