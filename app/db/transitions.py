"""Central task transitions with audit events, lease lifecycle, and fencing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, text
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

RUNNER_FENCED_TRIGGERS: frozenset[TaskTransition] = frozenset(
    {
        TaskTransition.START,
        TaskTransition.SUCCEED,
        TaskTransition.FAIL,
        TaskTransition.RETRY,
        TaskTransition.POLICY_BLOCK,
        TaskTransition.LEASE_EXPIRED,
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

OPERATOR_CANCEL_WITHOUT_LEASE: frozenset[TaskState] = frozenset(
    {
        TaskState.AWAITING_APPROVAL,
        TaskState.QUEUED,
        TaskState.BLOCKED,
    }
)


class TransitionActor(StrEnum):
    SYSTEM = "system"
    OPERATOR = "operator"
    RUNNER = "runner"


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
    """Map RETRY or lease expiry to FAIL when ``max_attempts`` is exhausted."""
    if trigger in (TaskTransition.RETRY, TaskTransition.LEASE_EXPIRED):
        if task.attempt >= max_attempts_for_task(task):
            return TaskTransition.FAIL
    return trigger


def _resolve_requeue_target(
    task: Task,
    trigger: TaskTransition,
    to_state: TaskState,
) -> TaskState:
    """
    Unapproved tasks return to ``awaiting_approval`` instead of a stuck ``queued`` row.
    """
    if to_state != TaskState.QUEUED:
        return to_state
    if trigger not in (TaskTransition.LEASE_EXPIRED, TaskTransition.RETRY):
        return to_state
    if task.requires_approval and task.approved_at is None:
        return TaskState.AWAITING_APPROVAL
    return to_state


def _touch_updated_at(task: Task, *, now: datetime) -> None:
    task.updated_at = now


def release_lease_for_task(session: Session, task_id: uuid.UUID) -> None:
    """Remove the active lease row for ``task_id`` (idempotent)."""
    session.execute(delete(Lease).where(Lease.task_id == task_id))


def _requires_active_lease(from_state: TaskState, trigger: TaskTransition) -> bool:
    if trigger == TaskTransition.ACQUIRE:
        return False
    if trigger == TaskTransition.CANCEL:
        return from_state in (TaskState.LEASED, TaskState.RUNNING)
    return trigger in RUNNER_FENCED_TRIGGERS


def _merge_event_detail(
    detail: dict[str, Any] | None,
    *,
    actor: TransitionActor,
    attempt: int,
    lease_id: uuid.UUID | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {"actor": actor.value}
    if lease_id is not None:
        merged["lease_id"] = str(lease_id)
    merged["attempt"] = attempt
    if detail:
        merged.update(detail)
    return merged


def _atomic_acquire(session: Session, *, task_id: uuid.UUID, now: datetime) -> Task | None:
    row = session.execute(
        text(
            """
            UPDATE tasks AS t
            SET status = 'leased',
                attempt = t.attempt + 1,
                updated_at = :now
            WHERE t.id = :task_id
              AND t.status = 'queued'
              AND t.cancel_requested = false
              AND (t.requires_approval = false OR t.approved_at IS NOT NULL)
            RETURNING t.id
            """
        ),
        {"task_id": task_id, "now": now},
    ).first()
    if row is None:
        return None
    task = session.get(Task, task_id)
    if task is not None:
        session.refresh(task)
    return task


def _atomic_runner_transition(
    session: Session,
    *,
    task_id: uuid.UUID,
    from_state: TaskState,
    to_state: TaskState,
    lease_id: uuid.UUID,
    now: datetime,
    increment_attempt: bool = False,
    allow_expired_lease: bool = False,
    allow_cancel_requested: bool = False,
) -> Task | None:
    """Fenced status change: task row and lease_id must match (lease expiry optional)."""
    expiry_clause = "l.expires_at <= :now" if allow_expired_lease else "l.expires_at > :now"
    cancel_clause = "true" if allow_cancel_requested else "t.cancel_requested = false"
    row = session.execute(
        text(
            f"""
            UPDATE tasks AS t
            SET status = CAST(:to_status AS task_state),
                updated_at = :now,
                attempt = CASE WHEN :increment_attempt THEN t.attempt + 1 ELSE t.attempt END
            FROM leases AS l
            WHERE t.id = :task_id
              AND t.status = CAST(:from_status AS task_state)
              AND l.task_id = t.id
              AND l.lease_id = :lease_id
              AND {expiry_clause}
              AND ({cancel_clause})
            RETURNING t.id
            """
        ),
        {
            "task_id": task_id,
            "from_status": from_state.value,
            "to_status": to_state.value,
            "lease_id": lease_id,
            "now": now,
            "increment_attempt": increment_attempt,
        },
    ).first()
    if row is None:
        return None
    task = session.get(Task, task_id)
    if task is not None:
        session.refresh(task)
    return task


def _atomic_simple_transition(
    session: Session,
    *,
    task_id: uuid.UUID,
    from_state: TaskState,
    to_state: TaskState,
    now: datetime,
) -> Task | None:
    row = session.execute(
        text(
            """
            UPDATE tasks
            SET status = CAST(:to_status AS task_state),
                updated_at = :now
            WHERE id = :task_id
              AND status = CAST(:from_status AS task_state)
            RETURNING id
            """
        ),
        {
            "task_id": task_id,
            "from_status": from_state.value,
            "to_status": to_state.value,
            "now": now,
        },
    ).first()
    if row is None:
        return None
    return session.get(Task, task_id)


def transition_task(
    session: Session,
    *,
    task: Task,
    trigger: TaskTransition,
    lease_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    actor: TransitionActor = TransitionActor.SYSTEM,
) -> TaskTransitionResult:
    """
    Apply ``trigger``, persist audit event, and maintain lease/attempt fields.

    Runner transitions with an active lease use atomic SQL fencing (status + lease_id + expiry).
    """
    effective_trigger = resolve_trigger(task, trigger)
    if effective_trigger != trigger:
        mapped_detail = dict(detail or {})
        if trigger == TaskTransition.RETRY:
            mapped_detail.setdefault("reason", "max_attempts_exhausted")
        elif trigger == TaskTransition.LEASE_EXPIRED:
            mapped_detail.setdefault("reason", "lease_expired_max_attempts")
        detail = mapped_detail
    current = task.status
    now = datetime.now(tz=UTC)

    if effective_trigger == TaskTransition.ENQUEUE:
        if current != TaskState.QUEUED:
            raise InvalidTaskTransitionError("enqueue is only valid for new queued tasks")
        result = apply_transition(None, TaskTransition.ENQUEUE)
        _touch_updated_at(task, now=now)
        record_transition_event(
            session,
            task_id=task.id,
            result=result,
            detail=_merge_event_detail(detail, actor=actor, attempt=task.attempt, lease_id=None),
            attempt=task.attempt,
            lease_id=None,
            actor=actor.value,
        )
        session.flush()
        return result

    if effective_trigger == TaskTransition.SUBMIT:
        if current != TaskState.AWAITING_APPROVAL:
            raise InvalidTaskTransitionError("submit is only valid for new tasks awaiting approval")
        result = apply_transition(None, TaskTransition.SUBMIT)
        _touch_updated_at(task, now=now)
        record_transition_event(
            session,
            task_id=task.id,
            result=result,
            detail=_merge_event_detail(detail, actor=actor, attempt=task.attempt, lease_id=None),
            attempt=task.attempt,
            lease_id=None,
            actor=actor.value,
        )
        session.flush()
        return result

    result = apply_transition(current, effective_trigger)
    target_state = _resolve_requeue_target(task, effective_trigger, result.to_state)
    if target_state != result.to_state:
        result = TaskTransitionResult(
            transition=result.transition,
            from_state=result.from_state,
            to_state=target_state,
            event_type=result.event_type,
        )

    if effective_trigger == TaskTransition.ACQUIRE:
        updated = _atomic_acquire(session, task_id=task.id, now=now)
        if updated is None:
            raise TransitionFencingError("acquire rejected: task is not eligible for lease")
        task.status = updated.status
        task.attempt = updated.attempt
        _touch_updated_at(task, now=now)
        record_transition_event(
            session,
            task_id=task.id,
            result=result,
            detail=_merge_event_detail(detail, actor=actor, attempt=task.attempt, lease_id=None),
            attempt=task.attempt,
            lease_id=None,
            actor=actor.value,
        )
        session.flush()
        return result

    if (
        effective_trigger == TaskTransition.CANCEL
        and current in OPERATOR_CANCEL_WITHOUT_LEASE
        and actor == TransitionActor.OPERATOR
    ):
        updated = _atomic_simple_transition(
            session,
            task_id=task.id,
            from_state=current,
            to_state=result.to_state,
            now=now,
        )
        if updated is None:
            raise InvalidTaskTransitionError("cancel rejected: task state changed")
        task.status = result.to_state
        _touch_updated_at(task, now=now)
        release_lease_for_task(session, task.id)
        task.lease = None
        record_transition_event(
            session,
            task_id=task.id,
            result=result,
            detail=_merge_event_detail(detail, actor=actor, attempt=task.attempt, lease_id=None),
            attempt=task.attempt,
            lease_id=None,
            actor=actor.value,
        )
        session.flush()
        return result

    if _requires_active_lease(current, effective_trigger):
        if lease_id is None:
            raise LeaseOwnershipError(f"{effective_trigger.value} requires lease_id")
        allow_expired = trigger in (
            TaskTransition.LEASE_EXPIRED,
            TaskTransition.CANCEL,
        ) and actor in (TransitionActor.SYSTEM, TransitionActor.OPERATOR)
        allow_cancel_flag = effective_trigger == TaskTransition.CANCEL and task.cancel_requested
        updated = _atomic_runner_transition(
            session,
            task_id=task.id,
            from_state=current,
            to_state=result.to_state,
            lease_id=lease_id,
            now=now,
            allow_expired_lease=allow_expired,
            allow_cancel_requested=allow_cancel_flag,
        )
        if updated is None:
            raise LeaseOwnershipError("lease_id does not match the active non-expired task lease")
        task.status = updated.status
        task.attempt = updated.attempt
        _touch_updated_at(task, now=now)
    else:
        task.status = result.to_state
        _touch_updated_at(task, now=now)

    if effective_trigger in RELEASE_LEASE_TRIGGERS or result.to_state in TERMINAL_STATES:
        release_lease_for_task(session, task.id)
        task.lease = None

    if result.to_state == TaskState.CANCELLED:
        task.cancel_requested = False
        task.cancel_reason = None

    record_transition_event(
        session,
        task_id=task.id,
        result=result,
        detail=_merge_event_detail(detail, actor=actor, attempt=task.attempt, lease_id=lease_id),
        attempt=task.attempt,
        lease_id=lease_id,
        actor=actor.value,
    )
    session.flush()
    return result


def request_operator_cancel(
    session: Session,
    *,
    task: Task,
    reason: str | None = None,
) -> TaskTransitionResult | None:
    """
    Operator cancel: immediate for pre-run states; ``cancel_requested`` for active leases.
    """
    if task.status in OPERATOR_CANCEL_WITHOUT_LEASE:
        return transition_task(
            session,
            task=task,
            trigger=TaskTransition.CANCEL,
            actor=TransitionActor.OPERATOR,
            detail={"reason": reason} if reason else None,
        )
    if task.status in (TaskState.LEASED, TaskState.RUNNING):
        task.cancel_requested = True
        task.cancel_reason = reason
        session.flush()
        record_transition_event(
            session,
            task_id=task.id,
            result=TaskTransitionResult(
                transition=TaskTransition.CANCEL,
                from_state=task.status,
                to_state=task.status,
                event_type="task.cancel_requested",
            ),
            detail=_merge_event_detail(
                {"reason": reason} if reason else None,
                actor=TransitionActor.OPERATOR,
                attempt=task.attempt,
                lease_id=task.lease.lease_id if task.lease else None,
            ),
            attempt=task.attempt,
            lease_id=task.lease.lease_id if task.lease else None,
            actor=TransitionActor.OPERATOR.value,
        )
        session.flush()
        return None
    raise InvalidTaskTransitionError(f"cancel is not allowed from {task.status.value!r}")


def fenced_transition_task(
    session: Session,
    *,
    task_id: uuid.UUID,
    from_state: TaskState,
    lease_id: uuid.UUID,
    trigger: TaskTransition,
    detail: dict[str, Any] | None = None,
    actor: TransitionActor = TransitionActor.RUNNER,
) -> Task | None:
    """Apply ``trigger`` with atomic lease fencing; return ``None`` when fencing fails."""
    task = session.get(Task, task_id)
    if task is None or task.status != from_state:
        return None
    try:
        transition_task(
            session,
            task=task,
            trigger=trigger,
            lease_id=lease_id,
            detail=detail,
            actor=actor,
        )
    except (LeaseOwnershipError, TransitionFencingError):
        return None
    session.refresh(task)
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
