"""Postgres queue access using ``SELECT … FOR UPDATE SKIP LOCKED``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.channels.receipt import settle_due_receipts
from app.core.schemas import DataClass, Worker
from app.db.attempt_outcome import (
    abandoned_detail,
    attempt_termination,
    record_abandoned_attempt,
)
from app.db.models import Lease, Task
from app.db.repository import record_gate_error
from app.db.routing import persist_task_route, pin_claim_worker, routing_snapshot
from app.db.settings import read_settings, try_lock_admission
from app.db.state_machine import (
    TERMINAL_STATES,
    InvalidTaskTransitionError,
    TaskState,
    TaskTransition,
)
from app.db.transitions import (
    LeaseOwnershipError,
    TransitionActor,
    TransitionFencingError,
    max_attempts_for_task,
    transition_task,
    upsert_lease,
)
from app.kb.snapshots import inspect_with_snapshots
from app.privacy.gate import PrivacyInputUnavailable
from app.routing.config import get_router_config
from app.workers.lane_status import LaneHealthState, update_lane_status


def dequeue_next_queued_task(
    session: Session,
    *,
    lease_ttl: timedelta,
    holder: str | None = None,
    worker: Worker,
    available_models: frozenset[str] | None = None,
) -> Task | None:
    """
    Atomically claim the oldest ``queued`` task and move it to ``leased``.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers receive distinct rows.
    ``available_models`` is the inventory a lane observed before this transaction.
    For such a lane, a configured model that is missing from the inventory is a
    transient outage and leaves the task queued, whereas a task kind without any
    configured model is a permanent configuration gap: the task is blocked with an
    audit event and the lane health records ``model_not_configured``.
    """
    if not isinstance(worker, Worker):
        raise ValueError("A known worker is required.")
    if lease_ttl.total_seconds() <= 0:
        raise ValueError("Lease duration must be positive.")
    config = get_router_config()
    if not try_lock_admission(session):
        return None
    controls = read_settings(session)
    if controls.paused or controls.stop_requested or controls.lane_paused[worker]:
        return None
    expire_overdue_tasks(session)
    active = (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_((TaskState.LEASED, TaskState.RUNNING)))
        )
        or 0
    )
    if active >= controls.max_parallel:
        return None
    stmt = (
        select(Task)
        .where(
            Task.status == TaskState.QUEUED,
            Task.approved_at.is_not(None),
            or_(Task.next_attempt_at.is_(None), Task.next_attempt_at <= func.clock_timestamp()),
            or_(Task.deadline_at.is_(None), Task.deadline_at > func.clock_timestamp()),
        )
        .order_by(Task.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    unavailable_ids: set[uuid.UUID] = set()
    while True:
        candidate = stmt.where(Task.id.not_in(unavailable_ids)) if unavailable_ids else stmt
        task = session.scalars(candidate).first()
        if task is None:
            return None
        if task.attempt >= max_attempts_for_task(task):
            unavailable_ids.add(task.id)
            continue
        if task.data_class == DataClass.LOCAL_ONLY and worker != Worker.OLLAMA:
            unavailable_ids.add(task.id)
            continue
        try:
            inspection = inspect_with_snapshots(session, task.payload)
        except PrivacyInputUnavailable as exc:
            record_gate_error(session, task=task, error=exc)
            unavailable_ids.add(task.id)
            continue
        if inspection.blocking_detail is not None:
            try:
                transition_task(
                    session,
                    task=task,
                    trigger=TaskTransition.BLOCK,
                    detail=inspection.blocking_detail,
                )
            except InvalidTaskTransitionError:
                if not expire_task_if_due(session, task=task):
                    raise
            continue
        snapshot = routing_snapshot(session)
        plan = persist_task_route(
            session,
            task,
            config=config,
            snapshot=snapshot,
            paused_lanes=frozenset(lane for lane, paused in controls.lane_paused.items() if paused),
        )
        if plan.status == "blocked":
            try:
                transition_task(
                    session,
                    task=task,
                    trigger=TaskTransition.BLOCK,
                    detail={"reason": "routing", "code": plan.reason},
                )
            except InvalidTaskTransitionError:
                if not expire_task_if_due(session, task=task):
                    raise
            continue
        if plan.status == "deferred":
            # A daily ceiling is temporary, unlike a terminal policy block.
            unavailable_ids.add(task.id)
            continue
        if task.lane != worker.value:
            unavailable_ids.add(task.id)
            continue
        if available_models is not None:
            model = plan.models.get(worker)
            if model is None:
                # No model for this task kind and no default: a later poll cannot
                # change this without a configuration change, so the task must not
                # wait silently. Block it without consuming an attempt.
                try:
                    transition_task(
                        session,
                        task=task,
                        trigger=TaskTransition.BLOCK,
                        detail={
                            "reason": "routing",
                            "code": "model_not_configured",
                            "lane": worker.value,
                            "kind": task.payload.get("kind"),
                        },
                    )
                except InvalidTaskTransitionError:
                    if not expire_task_if_due(session, task=task):
                        raise
                else:
                    update_lane_status(
                        session,
                        lane=worker,
                        status=LaneHealthState.UNAVAILABLE,
                        reason="model_not_configured",
                    )
                continue
            if model not in available_models:
                # The lane's external preflight observed this inventory before the
                # transaction. Missing or newly reconfigured models consume no claim.
                unavailable_ids.add(task.id)
                continue
        detail = {"lane": task.lane, "budget_date": snapshot.budget_date.isoformat()}
        if holder:
            detail["holder"] = holder
        try:
            transition_task(
                session,
                task=task,
                trigger=TaskTransition.ACQUIRE,
                detail=detail,
            )
            pin_claim_worker(task, worker)
            break
        except TransitionFencingError:
            # The deadline may pass after selection but before the atomic acquire.
            if not expire_task_if_due(session, task=task):
                raise

    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    lease_id = uuid.uuid4()
    upsert_lease(
        session,
        task_id=task.id,
        lease_id=lease_id,
        expires_at=now + lease_ttl,
        heartbeat_at=now,
        holder=holder,
    )
    session.refresh(task, attribute_names=["lease"])
    session.flush()
    return task


def reap_expired_leases(session: Session, *, limit: int = 32) -> int:
    """Close expired leases and record the attempt when the runner left none.

    An owner deadline still expires the task and does not invent a worker
    result. Cancellation stays cancellation. A vanished runner gets one
    ``worker_unavailable`` result, which is not a timeout, then the existing
    same-lane lease recovery until ``max_attempts``. That bound is the Etappe
    10 rule for lease expiry. A result the live runner submits, including
    ``worker_unavailable`` and ``timeout``, still goes through ``decide_failure``
    and may move to the next allowed lane. This sweep does not add another
    same-lane retry and does not skip that submitted-result rule. A result that
    already exists, including a timeout, is not rewritten. The same call settles
    due channel receipts, using one bounded batch.
    """
    expire_overdue_tasks(session, limit=limit)
    now = datetime.now(tz=UTC)
    settle_due_receipts(session, now=now, limit=limit)
    stmt = (
        select(Lease)
        .join(Task, Task.id == Lease.task_id)
        .where(
            Lease.expires_at < now,
            Task.status.in_((TaskState.LEASED, TaskState.RUNNING)),
        )
        .order_by(Lease.expires_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    leases = list(session.scalars(stmt).all())
    for lease in leases:
        task = lease.task
        try:
            if expire_task_if_due(session, task=task):
                continue
            if task.cancel_requested:
                termination = attempt_termination(session, task, if_absent="cancelled")
                transition_task(
                    session,
                    task=task,
                    trigger=TaskTransition.CANCEL,
                    lease_id=lease.lease_id,
                    actor=TransitionActor.SYSTEM,
                    detail=abandoned_detail(
                        termination=termination,
                        expired_at=lease.expires_at,
                        lease_id=lease.lease_id,
                    ),
                )
                record_abandoned_attempt(
                    session, task=task, lease=lease, now=now, termination="cancelled"
                )
                continue
            termination = attempt_termination(session, task, if_absent="process_lost")
            transition_task(
                session,
                task=task,
                trigger=TaskTransition.LEASE_EXPIRED,
                lease_id=lease.lease_id,
                detail=abandoned_detail(
                    termination=termination,
                    expired_at=lease.expires_at,
                    lease_id=lease.lease_id,
                ),
            )
            record_abandoned_attempt(
                session, task=task, lease=lease, now=now, termination="process_lost"
            )
        except (LeaseOwnershipError, TransitionFencingError):
            # The bounded sweep may omit this task, or its deadline may cross
            # after selection. Expire it without discarding the rest of the batch.
            if not expire_task_if_due(session, task=task):
                raise
    session.flush()
    return len(leases)


def expire_task_if_due(session: Session, *, task: Task) -> bool:
    """Expire a due owner deadline under the task lock; never infer one from a lease."""
    if task.deadline_at is None or task.status in TERMINAL_STATES:
        return False
    due = session.scalar(
        select(Task)
        .where(
            Task.id == task.id,
            Task.deadline_at <= func.clock_timestamp(),
            Task.status.not_in(TERMINAL_STATES),
        )
        .with_for_update(of=Task)
        .execution_options(populate_existing=True)
    )
    if due is None:
        return False
    transition_task(
        session,
        task=due,
        trigger=TaskTransition.EXPIRE,
        actor=TransitionActor.SYSTEM,
        detail={
            "reason": "owner_deadline_elapsed",
            "deadline_at": due.deadline_at.isoformat() if due.deadline_at else None,
        },
    )
    return True


def expire_overdue_tasks(session: Session, *, limit: int = 32) -> int:
    """Expire a bounded batch of due tasks with ``SKIP LOCKED`` and audit events."""
    due_tasks = session.scalars(
        select(Task)
        .where(
            Task.deadline_at <= func.clock_timestamp(),
            Task.status.not_in(TERMINAL_STATES),
        )
        .order_by(Task.deadline_at.asc(), Task.id.asc())
        .limit(limit)
        .with_for_update(of=Task, skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    for task in due_tasks:
        transition_task(
            session,
            task=task,
            trigger=TaskTransition.EXPIRE,
            actor=TransitionActor.SYSTEM,
            detail={
                "reason": "owner_deadline_elapsed",
                "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
            },
        )
    return len(due_tasks)
