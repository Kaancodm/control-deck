"""Worker-facing lease heartbeat and result submission helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.acceptance import evaluate_acceptance
from app.core.schemas import AcceptanceRule, ResultStatus, ResultV1
from app.core.validate import validate_result
from app.db.models import Lease, Task
from app.db.queue import expire_task_if_due
from app.db.repository import persist_result_v1, result_payload_for_storage
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import LeaseOwnershipError, fenced_transition_task

_RESULT_STATUS_TO_TRIGGER: dict[ResultStatus, TaskTransition] = {
    ResultStatus.SUCCEEDED: TaskTransition.SUCCEED,
    ResultStatus.FAILED: TaskTransition.FAIL,
    ResultStatus.CANCELLED: TaskTransition.CANCEL,
    ResultStatus.BLOCKED: TaskTransition.POLICY_BLOCK,
}


class WorkerOperationError(ValueError):
    """Worker request could not be applied."""


class TaskDeadlineElapsed(WorkerOperationError):
    """The owner's deadline expired; the expiry event must be committed."""


def refresh_lease_heartbeat(
    session: Session,
    *,
    lease_id: uuid.UUID,
    task_id: uuid.UUID,
    extend_by: timedelta,
) -> Lease:
    """Extend ``expires_at`` after row lock when the lease is still valid (DB clock)."""
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update(of=Task))
    if task is None:
        raise WorkerOperationError("task not found")
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        raise TaskDeadlineElapsed("owner deadline elapsed")
    stmt = (
        select(Lease)
        .join(Task, Task.id == Lease.task_id)
        .where(
            Lease.lease_id == lease_id,
            Lease.task_id == task_id,
            Task.status.in_((TaskState.LEASED, TaskState.RUNNING)),
        )
        .with_for_update(of=Lease)
    )
    lease = session.scalars(stmt).first()
    if lease is None:
        raise WorkerOperationError(
            "lease not found, expired, or fencing token does not match active lease"
        )

    now_db = session.scalar(text("SELECT clock_timestamp()"))
    if now_db is None:
        raise WorkerOperationError("database clock unavailable")
    if now_db.tzinfo is None:
        now_db = now_db.replace(tzinfo=UTC)
    if lease.expires_at <= now_db:
        raise WorkerOperationError(
            "lease not found, expired, or fencing token does not match active lease"
        )

    extend_seconds = extend_by.total_seconds()
    refreshed = session.execute(
        text(
            """
            UPDATE leases
            SET heartbeat_at = clock_timestamp(),
                expires_at = clock_timestamp() + CAST(:extend AS interval)
            WHERE lease_id = :lease_id AND task_id = :task_id
              AND EXISTS (
                  SELECT 1 FROM tasks AS t
                  WHERE t.id = :task_id
                    AND (t.deadline_at IS NULL OR t.deadline_at > clock_timestamp())
              )
            RETURNING lease_id
            """
        ),
        {
            "lease_id": lease_id,
            "task_id": task_id,
            "extend": f"{extend_seconds} seconds",
        },
    ).first()
    if refreshed is None:
        expire_task_if_due(session, task=task)
        raise TaskDeadlineElapsed("owner deadline elapsed")
    session.refresh(lease)
    session.flush()
    return lease


def submit_worker_result(
    session: Session,
    *,
    lease_id: uuid.UUID,
    result_payload: dict[str, Any],
) -> Task:
    """
    Validate ``result.v1``, persist the snapshot, and transition the task (fenced).

    Automatically moves ``leased`` tasks to ``running`` before applying the terminal trigger.
    """
    issues = validate_result(result_payload)
    if issues:
        raise WorkerOperationError(f"invalid result.v1: {issues}")

    result_v1 = ResultV1.model_validate(result_payload)
    task_id = result_v1.task_id

    trigger = _RESULT_STATUS_TO_TRIGGER[result_v1.status]
    stored_payload = result_payload_for_storage(result_v1)
    event_detail: dict[str, Any] = {"worker": result_v1.worker.value}
    if result_v1.status == ResultStatus.SUCCEEDED:
        # The payload is immutable. Evaluate it before acquiring task/lease locks
        # so heartbeats and owner control can proceed while checks are running.
        task_payload = session.scalar(select(Task.payload).where(Task.id == task_id))
        if task_payload is None:
            raise WorkerOperationError(f"task {task_id} not found")
        rules = [AcceptanceRule.model_validate(rule) for rule in task_payload["acceptance"]]
        acceptance = evaluate_acceptance(rules, result_v1.output.text)
        stored_payload["checks"] = [check.model_dump() for check in acceptance.checks]
        if not acceptance.passed:
            stored_payload["status"] = ResultStatus.FAILED.value
            stored_payload["error"] = {
                "code": "acceptance_failed",
                "message": "Core acceptance checks failed",
            }
            event_detail["reason"] = "acceptance_failed"
            trigger = TaskTransition.RETRY
    event_detail["result_status"] = stored_payload["status"]

    # Reload runtime state after evaluation: another transaction may have
    # cancelled, expired, or reassigned this task in the meantime.
    task = session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update(of=Task)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise WorkerOperationError(f"task {task_id} not found")

    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        raise TaskDeadlineElapsed("owner deadline elapsed")

    if result_v1.attempt != task.attempt:
        raise WorkerOperationError(
            f"result attempt {result_v1.attempt} does not match task attempt {task.attempt}"
        )

    if task.status == TaskState.LEASED:
        started = fenced_transition_task(
            session,
            task_id=task_id,
            from_state=TaskState.LEASED,
            lease_id=lease_id,
            trigger=TaskTransition.START,
        )
        if started is None:
            if expire_task_if_due(session, task=task):
                raise TaskDeadlineElapsed("owner deadline elapsed")
            raise LeaseOwnershipError("lease_id does not match the active task lease")
        task = started

    if task.status != TaskState.RUNNING:
        raise WorkerOperationError(
            f"task {task_id} must be running to accept a result (status={task.status.value})"
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
            raise TaskDeadlineElapsed("owner deadline elapsed")
        raise LeaseOwnershipError("result rejected: stale lease or task state")

    persist_result_v1(session, task=updated, result_payload=stored_payload)
    session.refresh(updated)
    return updated
