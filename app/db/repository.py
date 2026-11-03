"""Task persistence helpers aligned with validated ``task.v1`` payloads."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.schemas import TaskV1
from app.db.models import Task
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import transition_task


def create_task_from_v1(session: Session, task_v1: TaskV1) -> Task:
    """Insert a queued task row derived from a validated ``TaskV1`` model."""
    payload = task_v1.model_dump(mode="json")
    task = Task(
        id=task_v1.task_id,
        status=TaskState.QUEUED,
        data_class=task_v1.data_class,
        payload=payload,
        attempt=0,
    )
    session.add(task)
    session.flush()
    transition_task(
        session,
        task=task,
        trigger=TaskTransition.ENQUEUE,
        detail={"source": "create_task_from_v1"},
        skip_lease_check=True,
    )
    return task
