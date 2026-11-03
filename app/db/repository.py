"""Task persistence helpers aligned with validated ``task.v1`` payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.schemas import DataClass, ResultV1, TaskV1
from app.core.validate import validate_result, validate_task
from app.db.models import Result, Task
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import TransitionActor, transition_task


def create_task_from_v1(session: Session, task_v1: TaskV1) -> Task:
    """Insert a task awaiting owner approval derived from a validated ``TaskV1`` model."""
    existing = session.get(Task, task_v1.task_id)
    if existing is not None:
        return existing
    payload = task_v1.model_dump(mode="json")
    task = Task(
        id=task_v1.task_id,
        status=TaskState.AWAITING_APPROVAL,
        data_class=task_v1.data_class,
        payload=payload,
        attempt=0,
        requires_approval=True,
        approved_at=None,
    )
    session.add(task)
    session.flush()
    transition_task(
        session,
        task=task,
        trigger=TaskTransition.SUBMIT,
        detail={"source": "create_task_from_v1"},
    )
    return task


def create_task_from_api_payload(
    session: Session,
    payload: dict[str, Any],
) -> Task:
    """
    Validate ``task.v1``, persist a task awaiting owner approval, and audit.

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
        status=TaskState.AWAITING_APPROVAL,
        data_class=task_v1.data_class,
        payload=task_v1.model_dump(mode="json"),
        attempt=0,
        requires_approval=True,
        approved_at=None,
    )
    session.add(row)
    session.flush()
    transition_task(
        session,
        task=row,
        trigger=TaskTransition.SUBMIT,
        detail={"source": "api"},
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
        actor=TransitionActor.OPERATOR,
    )


def result_payload_for_storage(result_v1: ResultV1) -> dict[str, Any]:
    """Serialize ``result.v1`` for JSONB without explicit null token fields."""
    data = result_v1.model_dump(mode="json")
    usage = data.get("usage", {})
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens"):
            if usage.get(key) is None:
                usage.pop(key, None)
        data["usage"] = usage
    return data


def persist_result_v1(session: Session, *, task: Task, result_payload: dict[str, Any]) -> Result:
    """Append a validated ``result.v1`` row for ``task``."""
    issues = validate_result(result_payload)
    if issues:
        raise ValueError(issues)
    attempt = int(result_payload["attempt"])
    row = Result(
        task_id=task.id,
        attempt=attempt,
        payload=result_payload,
    )
    session.add(row)
    session.flush()
    return row
