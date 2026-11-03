"""Pydantic mirrors of reference_case.v1 JSON Schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import (
    AcceptanceRule,
    ContextRef,
    DataClass,
    Policy,
    RequestedBy,
    StrictModel,
    TaskInput,
    TaskKind,
    Worker,
)


class ReferenceCategory(StrEnum):
    NORMAL = "normal"
    TRAP = "trap"
    EDGE = "edge"


class ExpectStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class ReferenceTaskBody(StrictModel):
    """task.v1 fields without task_id and created_at."""

    schema_version: Literal["task.v1"]
    parent_task_id: UUID | None
    requested_by: RequestedBy
    kind: TaskKind
    title: str = Field(max_length=120)
    instructions: str = Field(min_length=1)
    inputs: list[TaskInput]
    context_refs: list[ContextRef]
    data_class: DataClass
    policy: Policy
    acceptance: list[AcceptanceRule]
    fallback_chain: list[Worker]
    labels: dict[str, str]

    @field_validator("fallback_chain")
    @classmethod
    def fallback_chain_non_empty_unique(cls, value: list[Worker]) -> list[Worker]:
        if len(value) < 1:
            raise ValueError("fallback_chain must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("fallback_chain entries must be unique")
        return value


class ReferenceExpect(StrictModel):
    status: ExpectStatus
    must_contain: list[str]
    must_not_contain: list[str]
    regex: str | None
    max_chars: int | None = Field(default=None, ge=1)
    max_seconds: float | None = Field(default=None, ge=0)
    allowed_workers: list[Worker]


class ReferenceCaseV1(StrictModel):
    id: str = Field(pattern=r"^[0-9]{3}-[a-z0-9-]+$")
    category: ReferenceCategory
    description: str = Field(min_length=1)
    task: ReferenceTaskBody
    expect: ReferenceExpect
