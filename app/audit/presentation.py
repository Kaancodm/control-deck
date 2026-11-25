"""Project append-only records without returning untrusted detail dictionaries."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.schemas import ErrorCode, Worker
from app.db.state_machine import TaskState, TaskTransition
from app.memory.reasons import CONVERSATION_REASONS, HOLD_CODES

TASK_EVENT_TYPES = frozenset(
    {f"task.{trigger.value}" for trigger in TaskTransition}
    | {"task.prepare", "task.cancel_requested", "task.gate_error", "kb.export"}
)
OPERATION_TYPES = frozenset({"backup", "restore", "retention", "update"})
AUTH_EVENT_TYPES = frozenset(
    {
        "auth.login_failed",
        "auth.login_throttled",
        "auth.login_locked",
        "auth.login_succeeded",
        "auth.logout",
        "auth.sessions_revoked",
        "auth.login_unlocked",
    }
)
ACTORS = frozenset({"system", "operator", "runner"})
PHASES = frozenset({"started", "artifacts_removed", "completed", "failed"})
_SAFE_REASONS = frozenset(code.value for code in ErrorCode) | {
    "artifact_conflict",
    "cancel_requested",
    "global_stop",
    "owner_deadline",
    "deadline_elapsed",
    "lease_expired",
    "privacy_gate",
    "routing",
    "file_unavailable",
    "context_unavailable",
    "payload_changed",
    "owner_pause",
    "attempts_exhausted",
    "restore_failed",
    "restore_partial_failure",
    "invalid_backup_set",
    "backup_retention_failed",
    "restic_failed",
    "update_failed",
    "update_rollback_failed",
}
_COUNT_KEYS = frozenset(
    {"tasks", "files", "maps", "snapshots", "backups", "bytes", "rows", "sessions"}
)
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_CONVERSATION_EVENT = re.compile(r"^(assistant|memory)\.[a-z][a-z0-9_]{0,48}\Z")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_REF_KEYS = frozenset(
    {
        "message_id",
        "existing_message_id",
        "reply_for_message_id",
        "fact_id",
        "thread_id",
        "link_id",
        "approval_id",
        "corrects_approval_id",
    }
)
_DIGEST_KEYS = frozenset({"text_sha256", "shown_sha256", "existing_text_sha256"})
_DECISIONS = frozenset({"approved", "rejected"})
_EVIDENCE = frozenset({"unbewiesen", "bewiesen", "abgelehnt"})


class AuditFilter(BaseModel):
    """Bound a single read/export page; dates are inclusive lower/exclusive upper."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["all", "task", "operation", "auth", "conversation"] = "all"
    event_type: str | None = None
    actor: Literal["system", "operator", "runner"] | None = None
    lane: Worker | None = None
    task_id: UUID | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_selection(self) -> AuditFilter:
        allowed = (
            TASK_EVENT_TYPES
            | AUTH_EVENT_TYPES
            | {f"operation.{value}" for value in OPERATION_TYPES}
        )
        if self.event_type is not None and self.event_type not in allowed:
            conversation = self.source == "conversation" and _CONVERSATION_EVENT.fullmatch(
                self.event_type
            )
            if not conversation:
                raise ValueError("Unknown audit event type")
        if self.since is not None and self.until is not None and self.since >= self.until:
            raise ValueError("Audit interval must be increasing")
        return self


class AuditItem(BaseModel):
    """A deliberately small projection; never serialize ORM records directly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["task", "operation", "auth", "conversation"]
    id: int | UUID
    created_at: datetime
    event_type: str
    task_id: UUID | None = None
    operation_id: UUID | None = None
    actor: str | None = None
    lane: Worker | None = None
    attempt: int | None = None
    from_status: TaskState | None = None
    to_status: TaskState | None = None
    phase: str | None = None
    reason: str | None = None
    error_code: ErrorCode | None = None
    payload_sha256: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    privacy_hold: str | None = None
    decision: str | None = None
    evidence_status: str | None = None
    refs: dict[str, str] = Field(default_factory=dict)
    digests: dict[str, str] = Field(default_factory=dict)
    cleared_valid_to: bool = False
    cleared_superseded_by: bool = False


class AuditPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: list[AuditItem]
    total: int
    limit: int
    offset: int


def _member(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def project_record(record: Mapping[str, object]) -> AuditItem:
    """Drop unknown actor/type/reason values instead of rendering database text."""
    source = record["source"]
    if source not in ("task", "operation", "auth", "conversation"):
        raise ValueError("Unknown audit record source")
    detail = record.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    worker = detail.get("lane") or detail.get("worker")
    lane = (
        Worker(worker)
        if isinstance(worker, str) and worker in {item.value for item in Worker}
        else None
    )
    error = _member(detail.get("error_code") or detail.get("code"), frozenset(ErrorCode))
    digest = detail.get("payload_sha256") or detail.get("sha256")
    digest = digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) else None
    counts = {
        key: value
        for key, value in detail.items()
        if key in _COUNT_KEYS and type(value) is int and 0 <= value <= 2**63 - 1
    }
    actor = _member(record.get("actor"), ACTORS)
    phase = _member(record.get("phase"), PHASES)
    event_type = record.get("event_type")
    allowed_types = (
        TASK_EVENT_TYPES | AUTH_EVENT_TYPES | {f"operation.{value}" for value in OPERATION_TYPES}
    )
    if source == "conversation":
        event_type = (
            event_type
            if isinstance(event_type, str) and _CONVERSATION_EVENT.fullmatch(event_type)
            else "unknown"
        )
    else:
        event_type = _member(event_type, allowed_types) or "unknown"
    hold = detail.get("privacy_hold") if source == "conversation" else None
    hold = hold if isinstance(hold, str) and hold in HOLD_CODES else None
    decision = detail.get("decision") if source == "conversation" else None
    decision = decision if isinstance(decision, str) and decision in _DECISIONS else None
    evidence = detail.get("evidence_status") if source == "conversation" else None
    evidence = evidence if isinstance(evidence, str) and evidence in _EVIDENCE else None
    refs = (
        {
            key: value
            for key, value in detail.items()
            if key in _REF_KEYS and isinstance(value, str) and _UUID.fullmatch(value)
        }
        if source == "conversation"
        else {}
    )
    digests = (
        {
            key: value
            for key, value in detail.items()
            if key in _DIGEST_KEYS and isinstance(value, str) and _DIGEST.fullmatch(value)
        }
        if source == "conversation"
        else {}
    )
    reason_codes = _SAFE_REASONS | (
        CONVERSATION_REASONS if source == "conversation" else frozenset()
    )
    reason = _member(detail.get("reason"), reason_codes)
    attempt = record.get("attempt")
    attempt = attempt if type(attempt) is int and 0 <= attempt <= 5 else None
    return AuditItem.model_validate(
        {
            "source": source,
            "id": record["id"],
            "created_at": record["created_at"],
            "event_type": event_type,
            "task_id": record.get("task_id"),
            "operation_id": record.get("operation_id"),
            "actor": actor,
            "lane": lane,
            "attempt": attempt,
            "from_status": record.get("from_status"),
            "to_status": record.get("to_status"),
            "phase": phase,
            "reason": reason,
            "error_code": error,
            "payload_sha256": digest,
            "counts": counts,
            "privacy_hold": hold,
            "decision": decision,
            "evidence_status": evidence,
            "refs": refs,
            "digests": digests,
            "cleared_valid_to": source == "conversation" and detail.get("cleared_valid_to") is True,
            "cleared_superseded_by": (
                source == "conversation" and detail.get("cleared_superseded_by") is True
            ),
        }
    )


def csv_cell(value: object) -> str:
    """Neutralize spreadsheet formulas, including whitespace/BOM-prefixed cells."""
    if value is None:
        return ""
    text = str(value)
    stripped = text.lstrip(" \t\r\n\v\f\ufeff")
    if text.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def audit_csv(items: Iterable[AuditItem]) -> str:
    """Export exactly the visible page and metadata columns, never hidden payloads."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    columns = (
        "created_at",
        "source",
        "id",
        "event_type",
        "task_id",
        "operation_id",
        "actor",
        "lane",
        "attempt",
        "from_status",
        "to_status",
        "phase",
        "reason",
        "error_code",
        "payload_sha256",
        "counts",
        "privacy_hold",
        "decision",
        "evidence_status",
        "refs",
        "digests",
        "cleared_valid_to",
        "cleared_superseded_by",
    )
    writer.writerow(columns)
    for index, item in enumerate(items):
        if index >= 200:
            raise ValueError("Audit export is limited to one page")
        data = item.model_dump(mode="json")
        data["counts"] = json.dumps(item.counts, sort_keys=True)
        data["refs"] = json.dumps(item.refs, sort_keys=True)
        data["digests"] = json.dumps(item.digests, sort_keys=True)
        data["cleared_valid_to"] = "true" if item.cleared_valid_to else ""
        data["cleared_superseded_by"] = "true" if item.cleared_superseded_by else ""
        writer.writerow(csv_cell(data[name]) for name in columns)
    return buffer.getvalue()
