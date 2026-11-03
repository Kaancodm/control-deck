"""Task persistence helpers aligned with validated ``task.v1`` payloads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.schemas import ResultV1, TaskV1
from app.core.validate import validate_result, validate_task
from app.db.models import Event, Result, Task
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import TransitionActor, transition_task
from app.privacy.gate import PrivacyInputUnavailable, inspect_stored_task, validate_submission

_GATE_ERROR_COOLDOWN = timedelta(seconds=60)


def create_task_from_v1(session: Session, task_v1: TaskV1) -> Task:
    """Insert a task awaiting owner approval derived from a validated ``TaskV1`` model."""
    existing = session.get(Task, task_v1.task_id)
    if existing is not None:
        return existing
    gate = validate_submission(task_v1)
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
    if gate.blocked:
        transition_task(session, task=task, trigger=TaskTransition.BLOCK, detail=gate.safe_detail())
    return task


def create_task_from_api_payload(
    session: Session,
    payload: dict[str, Any],
) -> Task:
    """Validate ``task.v1``, persist a task awaiting owner approval, and audit."""
    data = dict(payload)
    raw_deadline = data.pop("deadline_at", None)
    if raw_deadline is not None and not isinstance(raw_deadline, str):
        raise ValueError("deadline_at must be an ISO 8601 datetime with timezone or null")
    deadline_at = TypeAdapter(AwareDatetime | None).validate_python(
        datetime.fromisoformat(raw_deadline) if raw_deadline is not None else None
    )
    issues = validate_task(data)
    if issues:
        raise ValueError(issues)

    task_v1 = TaskV1.model_validate(data)
    if deadline_at is not None and session.scalar(
        select(literal(deadline_at) <= func.clock_timestamp())
    ):
        raise ValueError("deadline_at must be in the future")
    gate = validate_submission(task_v1)
    row = Task(
        id=task_v1.task_id,
        status=TaskState.AWAITING_APPROVAL,
        data_class=task_v1.data_class,
        payload=task_v1.model_dump(mode="json"),
        attempt=0,
        requires_approval=True,
        approved_at=None,
        deadline_at=deadline_at,
    )
    session.add(row)
    session.flush()
    transition_task(
        session,
        task=row,
        trigger=TaskTransition.SUBMIT,
        detail={"source": "api"},
    )
    if gate.blocked:
        transition_task(session, task=row, trigger=TaskTransition.BLOCK, detail=gate.safe_detail())
    return row


def approve_task(session: Session, task: Task) -> None:
    """Move an owner-gated task into the queue."""
    if task.status != TaskState.AWAITING_APPROVAL:
        raise ValueError(f"task {task.id} is not awaiting approval")
    try:
        inspection = inspect_stored_task(task.payload)
    except PrivacyInputUnavailable as exc:
        record_gate_error(session, task=task, error=exc, actor=TransitionActor.OPERATOR)
        raise
    if inspection.blocking_detail is not None:
        transition_task(
            session, task=task, trigger=TaskTransition.BLOCK, detail=inspection.blocking_detail
        )
        return
    task.approved_at = datetime.now(tz=UTC)
    transition_task(
        session,
        task=task,
        trigger=TaskTransition.APPROVE,
        detail={
            "source": "api",
            "file_hashes": [
                {"input_index": item.input_index, "sha256": item.sha256}
                for item in inspection.file_hashes
            ],
        },
        actor=TransitionActor.OPERATOR,
    )


def record_gate_error(
    session: Session,
    *,
    task: Task,
    error: PrivacyInputUnavailable,
    actor: TransitionActor = TransitionActor.SYSTEM,
) -> None:
    """Audit owner actions individually and rate-limit identical lease-poll errors."""
    now = None
    if actor == TransitionActor.SYSTEM:
        # Queue polling already holds this lock. Acquiring it here also protects
        # callers that record repeated errors directly in separate sessions.
        session.execute(select(Task.id).where(Task.id == task.id).with_for_update()).scalar_one()
        now = session.scalar(select(func.clock_timestamp()))
        assert now is not None
        recent = session.scalar(
            select(Event.id)
            .where(
                Event.task_id == task.id,
                Event.event_type == "task.gate_error",
                Event.actor == TransitionActor.SYSTEM.value,
                Event.created_at > now - _GATE_ERROR_COOLDOWN,
                Event.detail["code"].astext == error.code,
                Event.detail["source"].astext == error.source,
            )
            .limit(1)
        )
        if recent is not None:
            return
    event = Event(
        task_id=task.id,
        event_type="task.gate_error",
        from_status=task.status,
        to_status=task.status,
        actor=actor.value,
        attempt=task.attempt,
        detail={"reason": "privacy_gate", "code": error.code, "source": error.source},
    )
    if now is not None:
        # The event default uses transaction-start time, which may be stale in a
        # long-lived transaction. Keep the cooldown on the current database clock.
        event.created_at = now
    session.add(event)
    if actor == TransitionActor.SYSTEM:
        # Sessions disable autoflush. Make this insert visible to another poll
        # within the same transaction without committing it.
        session.flush()


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
