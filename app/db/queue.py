"""Postgres queue access using ``SELECT … FOR UPDATE SKIP LOCKED``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lease, Task
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import TransitionActor, transition_task, upsert_lease


def dequeue_next_queued_task(
    session: Session,
    *,
    lease_ttl: timedelta,
    holder: str | None = None,
) -> Task | None:
    """
    Atomically claim the oldest ``queued`` task and move it to ``leased``.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers receive distinct rows.
    """
    stmt = (
        select(Task)
        .where(
            Task.status == TaskState.QUEUED,
            Task.approved_at.is_not(None),
        )
        .order_by(Task.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None

    transition_task(
        session,
        task=task,
        trigger=TaskTransition.ACQUIRE,
        detail={"holder": holder} if holder else None,
    )

    now = datetime.now(tz=UTC)
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
    """
    Return expired leases to the queue (``LEASE_EXPIRED``) using ``SKIP LOCKED``.

    Only tasks in ``leased`` or ``running`` with ``expires_at < now()`` are processed.
    """
    now = datetime.now(tz=UTC)
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
        if task.cancel_requested:
            transition_task(
                session,
                task=task,
                trigger=TaskTransition.CANCEL,
                lease_id=lease.lease_id,
                actor=TransitionActor.SYSTEM,
                detail={"reaper": True, "reason": "cancel_requested"},
            )
            continue
        transition_task(
            session,
            task=task,
            trigger=TaskTransition.LEASE_EXPIRED,
            lease_id=lease.lease_id,
            detail={"reaper": True, "expired_at": lease.expires_at.isoformat()},
        )
    session.flush()
    return len(leases)
