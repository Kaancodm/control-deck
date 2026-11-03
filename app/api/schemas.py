"""OpenAPI response and request models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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


class TaskCreateOut(BaseModel):
    task: TaskDetailOut


class LeaseOut(BaseModel):
    lease_id: UUID
    task_id: UUID
    expires_at: datetime
    heartbeat_at: datetime
    holder: str | None


class WorkerLeaseResponse(BaseModel):
    task: TaskDetailOut
    lease: LeaseOut


class WorkerHeartbeatRequest(BaseModel):
    lease_id: UUID
    task_id: UUID
    extend_seconds: int | None = Field(default=None, ge=30, le=7200)


class WorkerHeartbeatResponse(BaseModel):
    lease: LeaseOut


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
