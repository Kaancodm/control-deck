"""Terminal result for an attempt whose runner did not submit one.

A lease expiry is not a timeout. Timeout remains the code a live runner stores
when its own budget elapses. This writer records that the process ended first.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.schemas import (
    SHA256_HEX,
    ErrorCode,
    RedactionInfo,
    ResultError,
    ResultOutput,
    ResultStatus,
    ResultTiming,
    ResultUsage,
    ResultV1,
    Worker,
)
from app.db.models import Event, Lease, Result, Task
from app.db.repository import persist_result_v1, result_payload_for_storage
from app.workers.lane_status import record_completed_attempt

Termination = Literal["process_lost", "cancelled", "timeout"]
_PROCESS_LOST = "Runner ended without a result."


def _payload_identity(session: Session, task: Task) -> tuple[str, bool, int]:
    event = session.scalar(
        select(Event)
        .where(
            Event.task_id == task.id,
            Event.event_type == "task.prepare",
            Event.attempt == task.attempt,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    detail = event.detail if event is not None and isinstance(event.detail, dict) else {}
    digest = detail.get("payload_sha256")
    applied = detail.get("redaction_applied")
    placeholders = detail.get("redaction_placeholders")
    if (
        isinstance(digest, str)
        and SHA256_HEX.fullmatch(digest)
        and isinstance(applied, bool)
        and type(placeholders) is int
        and placeholders >= 0
        and (applied or placeholders == 0)
    ):
        return (digest, applied, placeholders)
    encoded = json.dumps(
        task.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return (hashlib.sha256(encoded).hexdigest(), False, 0)


def _existing(session: Session, task: Task) -> Result | None:
    if task.attempt < 1:
        return None
    return session.scalar(
        select(Result).where(Result.task_id == task.id, Result.attempt == task.attempt)
    )


def termination_of(result: Result | None) -> Termination | None:
    """Classify a stored attempt without treating every failure as a timeout."""
    if result is None:
        return None
    status = result.payload.get("status")
    if status == ResultStatus.CANCELLED.value:
        return "cancelled"
    error = result.payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code == ErrorCode.TIMEOUT.value:
        return "timeout"
    return "process_lost"


def attempt_termination(
    session: Session,
    task: Task,
    *,
    if_absent: Literal["process_lost", "cancelled"],
) -> Termination:
    """Classify the current attempt without inserting a row."""
    found = termination_of(_existing(session, task))
    return found if found is not None else if_absent


def record_abandoned_attempt(
    session: Session,
    *,
    task: Task,
    lease: Lease,
    now: datetime,
    termination: Literal["process_lost", "cancelled"],
) -> Termination:
    """Insert at most one result for the current attempt and report its termination.

    An existing row is left unchanged, so a timeout the runner already stored
    cannot be rewritten as a lost process.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("attempt clock must be timezone-aware")
    moment = now.astimezone(UTC)
    existing = _existing(session, task)
    classified = termination_of(existing)
    if classified is not None:
        return classified
    try:
        worker = Worker(task.lane) if task.lane is not None else None
    except ValueError:
        worker = None
    if worker is None or task.attempt < 1:
        return termination
    started = lease.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    finished = moment if moment >= started else started
    digest, applied, placeholders = _payload_identity(session, task)
    failed = termination == "process_lost"
    result = ResultV1(
        schema_version="result.v1",
        task_id=task.id,
        attempt=task.attempt,
        worker=worker,
        status=ResultStatus.FAILED if failed else ResultStatus.CANCELLED,
        output=ResultOutput(text="", files=[]),
        checks=[],
        usage=ResultUsage(wall_seconds=max(0.0, (finished - started).total_seconds())),
        timing=ResultTiming(started_at=started, finished_at=finished),
        error=ResultError(code=ErrorCode.WORKER_UNAVAILABLE, message=_PROCESS_LOST)
        if failed
        else None,
        redaction=RedactionInfo(applied=applied, placeholders=placeholders),
        payload_hash=digest,
        log_ref=f"{task.id}/{task.attempt}/{worker.value}.log",
    )
    persist_result_v1(
        session,
        task=task,
        result_payload=result_payload_for_storage(result),
        execution_profile="unknown",
    )
    if failed:
        record_completed_attempt(
            session,
            lane=worker,
            status=ResultStatus.FAILED,
            error_code=ErrorCode.WORKER_UNAVAILABLE,
            execution_profile="unknown",
        )
    return termination


def abandoned_detail(
    *,
    termination: Termination,
    expired_at: datetime,
    lease_id: uuid.UUID,
) -> dict[str, object]:
    """Audit fields for the reaper. The error code is omitted for a timeout."""
    detail: dict[str, object] = {
        "reaper": True,
        "termination": termination,
        "expired_at": expired_at.isoformat(),
        "lease_id": str(lease_id),
    }
    if termination == "process_lost":
        detail["error_code"] = ErrorCode.WORKER_UNAVAILABLE.value
    elif termination == "cancelled":
        detail["reason"] = "cancel_requested"
    return detail
