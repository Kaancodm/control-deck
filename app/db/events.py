"""Persist audit events for task transitions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Event
from app.db.state_machine import TaskTransitionResult


def record_transition_event(
    session: Session,
    *,
    task_id: UUID,
    result: TaskTransitionResult,
    detail: dict[str, Any] | None = None,
) -> Event:
    """Append an audit row for a validated state transition."""
    event = Event(
        task_id=task_id,
        event_type=result.event_type,
        from_status=result.from_status,
        to_status=result.to_status,
        detail=detail,
    )
    session.add(event)
    return event
