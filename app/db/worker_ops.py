"""Lane-bound attempt control, explicit start and trusted result ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.acceptance import evaluate_acceptance
from app.core.retries import RetryDecision, canonical_chain, decide_failure
from app.core.schemas import (
    SHA256_HEX,
    AcceptanceRule,
    DataClass,
    ErrorCode,
    ResultStatus,
    ResultV1,
    TaskV1,
    Worker,
)
from app.core.validate import validate_result
from app.db.models import Event, Lease, Task
from app.db.queue import expire_task_if_due
from app.db.repository import persist_result_v1, result_payload_for_storage
from app.db.routing import retry_state_for_task
from app.db.settings import lock_admission, read_settings, update_settings
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import LeaseOwnershipError, fenced_transition_task
from app.redaction.engine import RedactionError
from app.redaction.repository import rehydrate_for_task
from app.workers.contracts import ControlAction, ControlDecision
from app.workers.lane_status import LaneHealthState, record_completed_attempt, update_lane_status

_RESULT_STATUS_TO_TRIGGER: dict[ResultStatus, TaskTransition] = {
    ResultStatus.SUCCEEDED: TaskTransition.SUCCEED,
    ResultStatus.FAILED: TaskTransition.FAIL,
    ResultStatus.CANCELLED: TaskTransition.CANCEL,
    ResultStatus.BLOCKED: TaskTransition.POLICY_BLOCK,
}
_ACTIVE_STATES = (TaskState.LEASED, TaskState.RUNNING)


class WorkerOperationError(ValueError):
    """Worker request could not be applied, without quoting untrusted contents."""


class TaskDeadlineElapsed(WorkerOperationError):
    """The owner's deadline expired; callers must commit the expiry before stopping."""


def _identity_matches(task: Task, worker: Worker, attempt: int) -> bool:
    if not isinstance(worker, Worker) or isinstance(attempt, bool) or attempt != task.attempt:
        return False
    if task.lane != worker.value:
        return False
    try:
        payload = TaskV1.model_validate(task.payload)
    except ValidationError:
        return False
    return (
        (payload.data_class != DataClass.LOCAL_ONLY or worker == Worker.OLLAMA)
        and (payload.data_class == DataClass.LOCAL_ONLY or worker in payload.fallback_chain)
        and worker not in payload.policy.forbidden_workers
        and (payload.policy.allowed_workers is None or worker in payload.policy.allowed_workers)
    )


def _preparation(
    session: Session, *, task: Task, lease_id: uuid.UUID, worker: Worker
) -> dict[str, Any]:
    event = session.scalar(
        select(Event)
        .where(
            Event.task_id == task.id,
            Event.event_type == "task.prepare",
            Event.attempt == task.attempt,
            Event.lease_id == lease_id,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    detail = event.detail if event is not None else None
    if (
        not isinstance(detail, dict)
        or detail.get("lane") != worker.value
        or detail.get("attempt") != task.attempt
        or detail.get("lease_id") != str(lease_id)
        or not isinstance(detail.get("payload_sha256"), str)
        or type(detail.get("redaction_applied")) is not bool
        or type(detail.get("redaction_placeholders")) is not int
    ):
        raise WorkerOperationError("Attempt has no matching prepared payload.")
    if (
        not SHA256_HEX.fullmatch(detail["payload_sha256"])
        or detail["redaction_placeholders"] < 0
        or (not detail["redaction_applied"] and detail["redaction_placeholders"] != 0)
    ):
        raise WorkerOperationError("Prepared payload metadata is invalid.")
    return detail


def control_attempt(
    session: Session,
    *,
    task_id: uuid.UUID,
    lease_id: uuid.UUID,
    attempt: int,
    worker: Worker,
    extend_by: timedelta | None = None,
) -> ControlDecision:
    """Check an attempt under task-then-lease locks; callers commit every decision.

    Deadline expiry is a normal returned decision so transaction wrappers do not
    accidentally roll it back. An expired lease is always ownership loss.
    """
    if extend_by is not None and not 0 < extend_by.total_seconds() <= 7200:
        raise WorkerOperationError("Heartbeat extension is outside the supported range.")
    task = session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update(of=Task)
        .execution_options(populate_existing=True)
    )
    if task is None or not _identity_matches(task, worker, attempt):
        return ControlDecision(ControlAction.LOST_LEASE)
    if task.status == TaskState.EXPIRED:
        try:
            _preparation(session, task=task, lease_id=lease_id, worker=worker)
        except WorkerOperationError:
            return ControlDecision(ControlAction.LOST_LEASE)
        return ControlDecision(ControlAction.EXPIRED)
    if task.status not in _ACTIVE_STATES:
        return ControlDecision(ControlAction.LOST_LEASE)
    lease = session.scalar(
        select(Lease)
        .where(Lease.task_id == task_id)
        .with_for_update(of=Lease)
        .execution_options(populate_existing=True)
    )
    if lease is None or lease.lease_id != lease_id:
        return ControlDecision(ControlAction.LOST_LEASE)
    if expire_task_if_due(session, task=task):
        return ControlDecision(ControlAction.EXPIRED)
    now = session.scalar(select(func.clock_timestamp()))
    if now is None or lease.expires_at <= now:
        return ControlDecision(ControlAction.LOST_LEASE)
    if task.cancel_requested or read_settings(session).stop_requested:
        return ControlDecision(ControlAction.CANCEL, lease.expires_at, lease.heartbeat_at)
    if extend_by is not None:
        refreshed = session.execute(
            text(
                """
                UPDATE leases AS l
                SET heartbeat_at = clock_timestamp(),
                    expires_at = clock_timestamp() + CAST(:extend AS interval)
                FROM tasks AS t
                WHERE l.lease_id = :lease_id AND l.task_id = :task_id
                  AND t.id = l.task_id
                  AND l.expires_at > clock_timestamp()
                  AND (t.deadline_at IS NULL OR t.deadline_at > clock_timestamp())
                RETURNING l.lease_id
                """
            ),
            {
                "lease_id": lease_id,
                "task_id": task_id,
                "extend": f"{extend_by.total_seconds()} seconds",
            },
        ).first()
        if refreshed is None:
            if expire_task_if_due(session, task=task):
                return ControlDecision(ControlAction.EXPIRED)
            return ControlDecision(ControlAction.LOST_LEASE)
        session.refresh(lease)
    return ControlDecision(ControlAction.CONTINUE, lease.expires_at, lease.heartbeat_at)


def start_worker_attempt(
    session: Session,
    *,
    task_id: uuid.UUID,
    lease_id: uuid.UUID,
    attempt: int,
    worker: Worker,
) -> ControlDecision:
    """Start a prepared, owned lease exactly once; commit before invoking a lane."""
    decision = control_attempt(
        session, task_id=task_id, lease_id=lease_id, attempt=attempt, worker=worker
    )
    if decision.action != ControlAction.CONTINUE:
        return decision
    task = session.get(Task, task_id)
    assert task is not None
    if task.status != TaskState.LEASED:
        raise WorkerOperationError("Attempt has already started.")
    _preparation(session, task=task, lease_id=lease_id, worker=worker)
    started = fenced_transition_task(
        session,
        task_id=task_id,
        from_state=TaskState.LEASED,
        lease_id=lease_id,
        trigger=TaskTransition.START,
        detail={"worker": worker.value},
    )
    if started is None:
        if expire_task_if_due(session, task=task):
            return ControlDecision(ControlAction.EXPIRED)
        return ControlDecision(ControlAction.LOST_LEASE)
    return decision


def acknowledge_worker_cancel(
    session: Session,
    *,
    task_id: uuid.UUID,
    lease_id: uuid.UUID,
    attempt: int,
    worker: Worker,
) -> ControlDecision:
    """Acknowledge a requested stop for an owned leased or running attempt."""
    decision = control_attempt(
        session, task_id=task_id, lease_id=lease_id, attempt=attempt, worker=worker
    )
    if decision.action != ControlAction.CANCEL:
        return decision
    task = session.get(Task, task_id)
    assert task is not None
    cancelled = fenced_transition_task(
        session,
        task_id=task_id,
        from_state=task.status,
        lease_id=lease_id,
        trigger=TaskTransition.CANCEL,
        detail={"worker": worker.value, "reason": "cancel_requested"},
    )
    if cancelled is None:
        if expire_task_if_due(session, task=task):
            return ControlDecision(ControlAction.EXPIRED)
        return ControlDecision(ControlAction.LOST_LEASE)
    return ControlDecision(ControlAction.CANCEL)


def refresh_lease_heartbeat(
    session: Session,
    *,
    lease_id: uuid.UUID,
    task_id: uuid.UUID,
    extend_by: timedelta,
) -> Lease:
    """Compatibility helper for trusted host callers; public APIs pass lane identity."""
    task = session.get(Task, task_id)
    if task is None:
        raise WorkerOperationError("Task not found.")
    try:
        worker = Worker(task.lane or "")
    except ValueError:
        raise WorkerOperationError("Task has no assigned worker.") from None
    decision = control_attempt(
        session,
        task_id=task_id,
        lease_id=lease_id,
        attempt=task.attempt,
        worker=worker,
        extend_by=extend_by,
    )
    if decision.action == ControlAction.EXPIRED:
        raise TaskDeadlineElapsed("Owner deadline elapsed.")
    if decision.action != ControlAction.CONTINUE:
        raise WorkerOperationError("Lease is unavailable, expired, or execution must stop.")
    lease = session.get(Lease, lease_id)
    assert lease is not None
    return lease


def _validate_result_identity(
    session: Session, *, task: Task, lease_id: uuid.UUID, result: ResultV1
) -> None:
    if not _identity_matches(task, result.worker, result.attempt):
        raise WorkerOperationError("Result identity does not match the assigned attempt.")
    if task.status != TaskState.RUNNING:
        raise WorkerOperationError("Attempt must be explicitly started before submitting a result.")
    if task.cancel_requested and result.status != ResultStatus.CANCELLED:
        raise LeaseOwnershipError("Result rejected: cancellation has been requested.")
    prepared = _preparation(session, task=task, lease_id=lease_id, worker=result.worker)
    if (
        result.payload_hash != prepared["payload_sha256"]
        or result.redaction.applied != prepared["redaction_applied"]
        or result.redaction.placeholders != prepared["redaction_placeholders"]
    ):
        raise WorkerOperationError("Result does not match the prepared payload metadata.")
    active = session.scalar(
        select(Lease.lease_id).where(
            Lease.task_id == task.id,
            Lease.lease_id == lease_id,
            Lease.expires_at > func.clock_timestamp(),
        )
    )
    if active is None:
        raise LeaseOwnershipError("Result rejected: ownership is no longer active.")


def submit_worker_result(
    session: Session,
    *,
    lease_id: uuid.UUID,
    result_payload: dict[str, Any],
) -> Task:
    """Rehydrate and validate a prepared attempt, then ingest it with fresh fencing."""
    if validate_result(result_payload):
        raise WorkerOperationError("Invalid result.v1 payload.")
    try:
        result_v1 = ResultV1.model_validate(result_payload)
    except ValidationError:
        raise WorkerOperationError("Invalid result.v1 payload.") from None
    task_id = result_v1.task_id
    task = session.scalar(
        select(Task).where(Task.id == task_id).execution_options(populate_existing=True)
    )
    if task is None:
        raise WorkerOperationError("Task not found.")
    # The early check uses no row locks; acceptance must not hold worker-control
    # locks. All mutable identity and lease conditions are checked again below.
    if task.status == TaskState.EXPIRED or expire_task_if_due(session, task=task):
        raise TaskDeadlineElapsed("Owner deadline elapsed.")
    _validate_result_identity(session, task=task, lease_id=lease_id, result=result_v1)
    trigger = _RESULT_STATUS_TO_TRIGGER[result_v1.status]
    stored_payload = result_payload_for_storage(result_v1)
    event_detail: dict[str, Any] = {"worker": result_v1.worker.value}
    redaction_failed = False
    if task.data_class == DataClass.CLOUD_REDACTED:
        try:
            stored_payload["output"]["text"] = rehydrate_for_task(
                session, task_id=task_id, text=result_v1.output.text
            )
        except RedactionError:
            redaction_failed = True
            stored_payload["output"]["text"] = ""
    if redaction_failed and result_v1.status != ResultStatus.CANCELLED:
        stored_payload["status"] = ResultStatus.FAILED.value
        stored_payload["error"] = {
            "code": "acceptance_failed",
            "message": "Local result redaction validation failed.",
        }
        stored_payload["checks"] = []
        event_detail["reason"] = "acceptance_failed"
    elif result_v1.status == ResultStatus.SUCCEEDED:
        rules = [AcceptanceRule.model_validate(rule) for rule in task.payload["acceptance"]]
        acceptance = evaluate_acceptance(rules, stored_payload["output"]["text"])
        stored_payload["checks"] = [check.model_dump() for check in acceptance.checks]
        if not acceptance.passed:
            stored_payload["status"] = ResultStatus.FAILED.value
            stored_payload["error"] = {
                "code": "acceptance_failed",
                "message": "Core acceptance checks failed.",
            }
            event_detail["reason"] = "acceptance_failed"
    event_detail["result_status"] = stored_payload["status"]
    normalized = ResultV1.model_validate(stored_payload)
    failure_code: ErrorCode | None = None
    if normalized.status in (ResultStatus.FAILED, ResultStatus.BLOCKED):
        # Typed provider errors use one policy even when the provider calls the
        # attempt blocked. Host privacy blocks happen before dispatch, outside
        # result ingestion. A blocked snapshot without an error stays blocked.
        failure_code = normalized.error.code if normalized.error is not None else None
    if failure_code in (ErrorCode.AUTH_REQUIRED, ErrorCode.POLICY_BLOCKED):
        # Claims and owner controls use this order too. Acceptance above runs
        # without either admission or task locks; rejected identities never pause.
        lock_admission(session)
    task = session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update(of=Task)
        .execution_options(populate_existing=True)
    )
    assert task is not None
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        raise TaskDeadlineElapsed("Owner deadline elapsed.")
    _validate_result_identity(session, task=task, lease_id=lease_id, result=result_v1)
    decision: RetryDecision | None = None
    next_attempt_at: datetime | None = None
    if failure_code is not None:
        try:
            payload = TaskV1.model_validate(task.payload)
            decision = decide_failure(
                error_code=failure_code,
                worker=result_v1.worker,
                attempt=task.attempt,
                max_attempts=payload.policy.max_attempts,
                chain=canonical_chain(payload),
                state=retry_state_for_task(task),
            )
        except ValueError:
            raise WorkerOperationError("Retry metadata is invalid.") from None
        if decision.action == "fallback" and isinstance(task.plan, dict):
            exclusions = task.plan.get("exclusions")
            remaining = canonical_chain(payload)[decision.state.cursor :]
            if (
                isinstance(exclusions, dict)
                and remaining
                and all(exclusions.get(worker.value) == "rule_not_allowed" for worker in remaining)
            ):
                # Use the rule that admitted this attempt, not temporary lane
                # availability. A structurally exhausted chain must terminate.
                decision = decision.model_copy(
                    update={"action": "fail", "reason": "fallback_exhausted"}
                )
        if failure_code == ErrorCode.ACCEPTANCE_FAILED:
            # Contract: acceptance_failed -> review. The result stays stored as
            # failed with the effective code; the owner decides, nobody retries.
            trigger = TaskTransition.REVIEW
        elif decision.action == "fail":
            trigger = TaskTransition.FAIL
        else:
            trigger = TaskTransition.RETRY
        if decision.action != "fail":
            now = session.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            next_attempt_at = now + timedelta(seconds=decision.delay_seconds)
        event_detail.update(
            reason=failure_code.value,
            retry_reason=decision.reason,
            retry_action=decision.action,
            delay_seconds=decision.delay_seconds,
            next_attempt_at=next_attempt_at.isoformat() if next_attempt_at is not None else None,
            lane_paused=decision.pause_lane,
        )
    updated = fenced_transition_task(
        session,
        task_id=task_id,
        from_state=TaskState.RUNNING,
        lease_id=lease_id,
        trigger=trigger,
        detail=event_detail,
    )
    if updated is None:
        if expire_task_if_due(session, task=task):
            raise TaskDeadlineElapsed("Owner deadline elapsed.")
        raise LeaseOwnershipError("Result rejected: stale lease, control request or task state.")
    persist_result_v1(session, task=updated, result_payload=stored_payload)
    if decision is not None:
        updated.retry_state = decision.state.model_dump(mode="json")
        updated.next_attempt_at = next_attempt_at
        if decision.pause_lane:
            assert failure_code is not None
            update_settings(session, {f"lanes.{result_v1.worker.value}.paused": True})
            update_lane_status(
                session,
                lane=result_v1.worker,
                status=(
                    LaneHealthState.AUTH_REQUIRED
                    if failure_code == ErrorCode.AUTH_REQUIRED
                    else LaneHealthState.PAUSED
                ),
                reason=failure_code.value,
            )
    else:
        updated.next_attempt_at = None
    record_completed_attempt(
        session, lane=normalized.worker, status=normalized.status, error_code=failure_code
    )
    session.flush()
    session.refresh(updated)
    return updated
