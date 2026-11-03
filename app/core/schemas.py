"""Pydantic mirrors of task.v1 and result.v1 JSON Schemas."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.paths import DECK_RELATIVE_PATH, validate_deck_relative_path

SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")
RELATIVE_FILE_PATH = DECK_RELATIVE_PATH


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
            if self.path is not None:
                raise ValueError("text inputs must not include path")
        elif self.type == InputType.FILE:
            if self.path is None:
                raise ValueError("file inputs require path")
            validate_deck_relative_path(self.path, field="inputs[].path")
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

    @field_validator("allowed_workers")
    @classmethod
    def allowed_workers_unique(cls, value: list[Worker] | None) -> list[Worker] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("allowed_workers entries must be unique")
        return value

    @model_validator(mode="after")
    def validate_worker_sets(self) -> Policy:
        allowed = set(self.allowed_workers or [])
        forbidden = set(self.forbidden_workers)
        overlap = allowed & forbidden
        if overlap:
            raise ValueError(
                f"allowed_workers and forbidden_workers overlap: {sorted(w.value for w in overlap)}"
            )
        return self


class AcceptanceRule(StrictModel):
    type: AcceptanceType
    value: str | None = None


class TaskV1(StrictModel):
    schema_version: Literal["task.v1"]
    task_id: UUID
    parent_task_id: UUID | None
    created_at: AwareDatetime
    requested_by: RequestedBy
    kind: TaskKind
    title: str = Field(min_length=1, max_length=120)
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

    @model_validator(mode="after")
    def validate_fallback_vs_forbidden(self) -> TaskV1:
        forbidden = set(self.policy.forbidden_workers)
        blocked = [worker for worker in self.fallback_chain if worker in forbidden]
        if blocked:
            raise ValueError(
                "fallback_chain must not include forbidden workers: "
                + ", ".join(worker.value for worker in blocked)
            )
        return self


class OutputFile(StrictModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_deck_relative_path(value, field="output.files[].path")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not SHA256_HEX.fullmatch(value):
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

    @field_validator("input_tokens", "output_tokens", mode="before")
    @classmethod
    def reject_null_token_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("token counts must be omitted or a non-null integer")
        return value


class ResultTiming(StrictModel):
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def validate_order(self) -> ResultTiming:
        if self.finished_at < self.started_at:
            raise ValueError("timing.finished_at must not be before timing.started_at")
        return self


class ResultError(StrictModel):
    code: ErrorCode
    message: str = Field(min_length=1)


class RedactionInfo(StrictModel):
    applied: bool
    placeholders: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_placeholders(self) -> RedactionInfo:
        if not self.applied and self.placeholders > 0:
            raise ValueError("redaction.placeholders must be 0 when redaction.applied is false")
        return self


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

    @field_validator("log_ref")
    @classmethod
    def validate_log_ref(cls, value: str) -> str:
        return validate_deck_relative_path(value, field="log_ref")

    @field_validator("payload_hash")
    @classmethod
    def validate_payload_hash(cls, value: str) -> str:
        if not SHA256_HEX.fullmatch(value):
            raise ValueError("payload_hash must be a 64-character lowercase hex string")
        return value

    @model_validator(mode="after")
    def validate_error_vs_status(self) -> ResultV1:
        if self.status == ResultStatus.FAILED and self.error is None:
            raise ValueError("failed status requires a non-null error")
        if self.status == ResultStatus.SUCCEEDED and self.error is not None:
            raise ValueError("succeeded status requires error to be null")
        return self
