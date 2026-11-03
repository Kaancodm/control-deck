"""Pydantic mirrors of task.v1 and result.v1 JSON Schemas."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
RELATIVE_FILE_PATH = re.compile(r"^(?!/)(?!.*\.\.).+$")


class RequestedBy(StrEnum):
    UI = "ui"
    API = "api"
    N8N = "n8n"
    SYSTEM = "system"


class TaskKind(StrEnum):
    RESEARCH = "research"
    SUMMARIZE = "summarize"
    DRAFT = "draft"
    CODE = "code"
    REVIEW = "review"
    CLASSIFY = "classify"
    CUSTOM = "custom"


class InputType(StrEnum):
    TEXT = "text"
    FILE = "file"


class DataClass(StrEnum):
    LOCAL_ONLY = "local_only"
    CLOUD_REDACTED = "cloud_redacted"
    CLOUD_OK = "cloud_ok"


class Worker(StrEnum):
    OLLAMA = "ollama"
    CLAUDE = "claude"
    CODEX = "codex"


class ContextSource(StrEnum):
    KNOWLEDGE_DB = "knowledge_db"


class AcceptanceType(StrEnum):
    MUST_CONTAIN = "must_contain"
    MUST_NOT_CONTAIN = "must_not_contain"
    REGEX = "regex"
    JSON_SCHEMA = "json_schema"
    MAX_CHARS = "max_chars"
    HUMAN = "human"


class ResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class ErrorCode(StrEnum):
    WORKER_UNAVAILABLE = "worker_unavailable"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    POLICY_BLOCKED = "policy_blocked"
    ACCEPTANCE_FAILED = "acceptance_failed"
    INTERNAL = "internal"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class TaskInput(StrictModel):
    name: str = Field(min_length=1)
    type: InputType
    value: str | None = None
    path: str | None = None
    mime: str | None = None

    @model_validator(mode="after")
    def validate_input_shape(self) -> TaskInput:
        if self.type == InputType.TEXT:
            if self.value is None:
                raise ValueError("text inputs require value")
        elif self.type == InputType.FILE:
            if self.path is None:
                raise ValueError("file inputs require path")
            if not RELATIVE_FILE_PATH.match(self.path):
                raise ValueError("file path must be relative and must not contain '..'")
        return self


class ContextRef(StrictModel):
    source: ContextSource
    ref: str = Field(min_length=1)


class Policy(StrictModel):
    allowed_workers: list[Worker] | None = None
    forbidden_workers: list[Worker] = Field(default_factory=list)
    max_attempts: int = Field(default=3, ge=1, le=5)
    timeout_s: int = Field(default=900, ge=30, le=7200)
    require_review: bool = True


class AcceptanceRule(StrictModel):
    type: AcceptanceType
    value: str | None = None


class TaskV1(StrictModel):
    schema_version: Literal["task.v1"]
    task_id: UUID
    parent_task_id: UUID | None
    created_at: datetime
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


class OutputFile(StrictModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not SHA256_HEX.match(value):
            raise ValueError("sha256 must be a 64-character lowercase hex string")
        return value


class ResultOutput(StrictModel):
    text: str
    files: list[OutputFile]


class ResultCheck(StrictModel):
    type: str = Field(min_length=1)
    passed: bool
    detail: str


class ResultUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    wall_seconds: float = Field(ge=0)


class ResultTiming(StrictModel):
    started_at: datetime
    finished_at: datetime


class ResultError(StrictModel):
    code: ErrorCode
    message: str = Field(min_length=1)


class RedactionInfo(StrictModel):
    applied: bool
    placeholders: int = Field(ge=0)


class ResultV1(StrictModel):
    schema_version: Literal["result.v1"]
    task_id: UUID
    attempt: int = Field(ge=1)
    worker: Worker
    status: ResultStatus
    output: ResultOutput
    checks: list[ResultCheck]
    usage: ResultUsage
    timing: ResultTiming
    error: ResultError | None
    redaction: RedactionInfo
    payload_hash: str
    log_ref: str = Field(min_length=1)

    @field_validator("payload_hash")
    @classmethod
    def validate_payload_hash(cls, value: str) -> str:
        if not SHA256_HEX.match(value):
            raise ValueError("payload_hash must be a 64-character lowercase hex string")
        return value

    @model_validator(mode="after")
    def validate_error_vs_status(self) -> ResultV1:
        if self.status == ResultStatus.FAILED and self.error is None:
            raise ValueError("failed status requires a non-null error")
        if self.status == ResultStatus.SUCCEEDED and self.error is not None:
            raise ValueError("succeeded status requires error to be null")
        return self
