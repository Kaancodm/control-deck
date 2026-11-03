"""Central task transitions with audit events, lease lifecycle, and fencing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.events import record_transition_event
from app.db.models import Lease, Task
from app.db.state_machine import (
    TERMINAL_STATES,
    InvalidTaskTransitionError,
    TaskState,
    TaskTransition,
    TaskTransitionResult,
    apply_transition,
)

LEASE_FENCED_TRIGGERS: frozenset[TaskTransition] = frozenset(
    {
        TaskTransition.START,
        TaskTransition.SUCCEED,
        TaskTransition.FAIL,
        TaskTransition.RETRY,
        TaskTransition.POLICY_BLOCK,
        TaskTransition.LEASE_EXPIRED,
        TaskTransition.CANCEL,
    }
)

RELEASE_LEASE_TRIGGERS: frozenset[TaskTransition] = frozenset(
    {
        TaskTransition.LEASE_EXPIRED,
        TaskTransition.RETRY,
        TaskTransition.FAIL,
        TaskTransition.SUCCEED,
        TaskTransition.POLICY_BLOCK,
        TaskTransition.CANCEL,
        TaskTransition.BLOCK,
    }
)


class LeaseOwnershipError(PermissionError):
    """Raised when a worker does not hold the expected lease."""


class TransitionFencingError(PermissionError):
    """Raised when optimistic fencing rejects a transition."""


def max_attempts_for_task(task: Task) -> int:
    policy = task.payload.get("policy")
    if isinstance(policy, dict) and "max_attempts" in policy:
        return int(policy["max_attempts"])
    return 3


def resolve_trigger(task: Task, trigger: TaskTransition) -> TaskTransition:
    """Map RETRY to FAIL when ``max_attempts`` is exhausted."""
    if trigger == TaskTransition.RETRY and task.attempt >= max_attempts_for_task(task):
        return TaskTransition.FAIL
    return trigger


def _touch_updated_at(task: Task, *, now: datetime) -> None:
    task.updated_at = now


def release_lease_for_task(session: Session, task_id: uuid.UUID) -> None:
    """Remove the active lease row for ``task_id`` (idempotent)."""
    session.execute(delete(Lease).where(Lease.task_id == task_id))


def transition_task(
    session: Session,
    *,
    task: Task,
    trigger: TaskTransition,
    lease_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    skip_lease_check: bool = False,
) -> TaskTransitionResult:
    """
    Apply ``trigger``, persist audit event, and maintain lease/attempt fields.

    All callers should use this instead of mutating ``task.status`` directly.
    """
    effective_trigger = resolve_trigger(task, trigger)
    current = task.status

    if effective_trigger == TaskTransition.ENQUEUE:
        if current != TaskState.QUEUED:
            raise InvalidTaskTransitionError("enqueue is only valid for new queued tasks")
        result = apply_transition(None, TaskTransition.ENQUEUE)
        now = datetime.now(tz=UTC)
        _touch_updated_at(task, now=now)
        record_transition_event(session, task_id=task.id, result=result, detail=detail)
        session.flush()
        return result

    result = apply_transition(current, effective_trigger)
    now = datetime.now(tz=UTC)

    if not skip_lease_check and effective_trigger in LEASE_FENCED_TRIGGERS:
        if lease_id is None:
            raise LeaseOwnershipError(f"{effective_trigger.value} requires lease_id")
        if task.lease is None or task.lease.lease_id != lease_id:
            raise LeaseOwnershipError("lease_id does not match the active task lease")

    if effective_trigger == TaskTransition.ACQUIRE:
        task.attempt += 1

    task.status = result.to_state
    _touch_updated_at(task, now=now)

    if effective_trigger in RELEASE_LEASE_TRIGGERS or result.to_state in TERMINAL_STATES:
        release_lease_for_task(session, task.id)
        task.lease = None

    record_transition_event(session, task_id=task.id, result=result, detail=detail)
    session.flush()
    return result


def fenced_transition_task(
    session: Session,
    *,
    task_id: uuid.UUID,
    from_state: TaskState,
    lease_id: uuid.UUID,
    trigger: TaskTransition,
    detail: dict[str, Any] | None = None,
) -> Task | None:
    """
    Apply ``trigger`` only if the row is still in ``from_state`` with ``lease_id``.

    Locks the task row and verifies lease ownership before transitioning.
    """
    stmt = (
        select(Task)
        .join(Lease, Lease.task_id == Task.id)
        .where(
            Task.id == task_id,
            Task.status == from_state,
            Lease.lease_id == lease_id,
        )
        .with_for_update(of=Task)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None

    transition_task(
        session,
        task=task,
        trigger=trigger,
        lease_id=lease_id,
        detail=detail,
    )
    return task


def upsert_lease(
    session: Session,
    *,
    task_id: uuid.UUID,
    lease_id: uuid.UUID,
    expires_at: datetime,
    heartbeat_at: datetime,
    holder: str | None,
) -> Lease:
    """Create or replace the single lease row for ``task_id`` (re-acquire safe)."""
    release_lease_for_task(session, task_id)
    lease = Lease(
        lease_id=lease_id,
        task_id=task_id,
        expires_at=expires_at,
        heartbeat_at=heartbeat_at,
        holder=holder,
    )
    session.add(lease)
    session.flush()
    return lease
