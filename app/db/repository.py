"""Task persistence helpers aligned with validated ``task.v1`` payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.schemas import DataClass, TaskV1
from app.core.validate import validate_task
from app.db.models import Result, Task
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
        requires_approval=False,
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


def create_task_from_api_payload(
    session: Session,
    payload: dict[str, Any],
    *,
    requires_approval: bool = True,
) -> Task:
    """
    Validate ``task.v1``, persist a task awaiting owner approval (default), and audit.

    Applies ``data_class=local_only`` when the field is omitted before validation.
    """
    data = dict(payload)
    if "data_class" not in data:
        data["data_class"] = DataClass.LOCAL_ONLY.value

    issues = validate_task(data)
    if issues:
        raise ValueError(issues)

    task_v1 = TaskV1.model_validate(data)
    row = Task(
        id=task_v1.task_id,
        status=TaskState.AWAITING_APPROVAL if requires_approval else TaskState.QUEUED,
        data_class=task_v1.data_class,
        payload=task_v1.model_dump(mode="json"),
        attempt=0,
        requires_approval=requires_approval,
        approved_at=None if requires_approval else datetime.now(tz=UTC),
    )
    session.add(row)
    session.flush()
    trigger = TaskTransition.SUBMIT if requires_approval else TaskTransition.ENQUEUE
    transition_task(
        session,
        task=row,
        trigger=trigger,
        detail={"source": "api"},
        skip_lease_check=True,
    )
    return row


def approve_task(session: Session, task: Task) -> None:
    """Move an owner-gated task into the queue."""
    if task.status != TaskState.AWAITING_APPROVAL:
        raise ValueError(f"task {task.id} is not awaiting approval")
    task.approved_at = datetime.now(tz=UTC)
    transition_task(
        session,
        task=task,
        trigger=TaskTransition.APPROVE,
        detail={"source": "api"},
        skip_lease_check=True,
    )


def persist_result_v1(session: Session, *, task: Task, result_payload: dict[str, Any]) -> Result:
    """Append a validated ``result.v1`` row for ``task``."""
    attempt = int(result_payload["attempt"])
    row = Result(
        task_id=task.id,
        attempt=attempt,
        payload=result_payload,
    )
    session.add(row)
    session.flush()
    return row
