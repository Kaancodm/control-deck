"""Worker-facing lease heartbeat and result submission helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.core.schemas import ResultStatus, ResultV1
from app.core.validate import validate_result
from app.db.models import Lease, Task
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


def refresh_lease_heartbeat(
    session: Session,
    *,
    lease_id: uuid.UUID,
    task_id: uuid.UUID,
    extend_by: timedelta,
) -> Lease:
    """Extend ``expires_at`` when the lease is still valid and the fence token matches."""
    now = datetime.now(tz=UTC)
    new_expires = now + extend_by
    active_task = exists(
        select(1).where(
            Task.id == Lease.task_id,
            Task.id == task_id,
            Task.status.in_((TaskState.LEASED, TaskState.RUNNING)),
        )
    )
    stmt = (
        update(Lease)
        .where(
            Lease.lease_id == lease_id,
            Lease.task_id == task_id,
            active_task,
            Lease.expires_at > now,
        )
        .values(heartbeat_at=now, expires_at=new_expires)
        .returning(Lease)
    )
    lease = session.scalars(stmt).first()
    if lease is None:
        raise WorkerOperationError(
            "lease not found, expired, or fencing token does not match active lease"
        )
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

    task = session.get(Task, task_id)
    if task is None:
        raise WorkerOperationError(f"task {task_id} not found")

    if task.status == TaskState.LEASED:
        started = fenced_transition_task(
            session,
            task_id=task_id,
            from_state=TaskState.LEASED,
            lease_id=lease_id,
            trigger=TaskTransition.START,
        )
        if started is None:
            raise LeaseOwnershipError("lease_id does not match the active task lease")
        task = started

    if task.status != TaskState.RUNNING:
        raise WorkerOperationError(
            f"task {task_id} must be running to accept a result (status={task.status.value})"
        )

    if result_v1.attempt != task.attempt:
        raise WorkerOperationError(
            f"result attempt {result_v1.attempt} does not match task attempt {task.attempt}"
        )

    trigger = _RESULT_STATUS_TO_TRIGGER.get(result_v1.status)
    if trigger is None:
        raise WorkerOperationError(f"unsupported result status {result_v1.status.value}")

    stored_payload = result_payload_for_storage(result_v1)
    persist_result_v1(session, task=task, result_payload=stored_payload)

    updated = fenced_transition_task(
        session,
        task_id=task_id,
        from_state=TaskState.RUNNING,
        lease_id=lease_id,
        trigger=trigger,
        detail={"worker": result_v1.worker.value, "result_status": result_v1.status.value},
    )
    if updated is None:
        raise LeaseOwnershipError("result rejected: stale lease or task state")

    session.refresh(updated)
    return updated
