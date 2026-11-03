"""Persist audit events for task transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Event
from app.db.state_machine import TaskState, TaskTransitionResult


def record_transition_event(
    session: Session,
    *,
    task_id: UUID,
    result: TaskTransitionResult,
    detail: dict[str, Any] | None = None,
    actor: str | None = None,
    attempt: int | None = None,
    lease_id: UUID | None = None,
) -> Event:
    """Append an audit row for a validated state transition."""
    from_state = TaskState(result.from_status) if result.from_status is not None else None
    to_state = TaskState(result.to_status)
    event = Event(
        task_id=task_id,
        event_type=result.event_type,
        from_status=from_state,
        to_status=to_state,
        detail=detail,
        actor=actor,
        attempt=attempt,
        lease_id=lease_id,
    )
    if result.event_type == "task.acquire":
        # A claim belongs to its admission day, not the transaction start day.
        claimed_at = session.scalar(select(func.clock_timestamp()))
        assert isinstance(claimed_at, datetime)
        event.created_at = claimed_at
        event.detail = {
            **(event.detail or {}),
            "budget_date": claimed_at.astimezone(UTC).date().isoformat(),
        }
    session.add(event)
    return event
