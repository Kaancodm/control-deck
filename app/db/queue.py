"""Postgres queue access using ``SELECT … FOR UPDATE SKIP LOCKED``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lease, Task
from app.db.state_machine import TaskState, TaskTransition, apply_transition


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
        .where(Task.status == TaskState.QUEUED)
        .order_by(Task.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None

    transition = apply_transition(task.status, TaskTransition.ACQUIRE)
    task.status = transition.to_state

    now = datetime.now(tz=UTC)
    lease = Lease(
        lease_id=uuid.uuid4(),
        task_id=task.id,
        expires_at=now + lease_ttl,
        heartbeat_at=now,
        holder=holder,
    )
    session.add(lease)
    return task
