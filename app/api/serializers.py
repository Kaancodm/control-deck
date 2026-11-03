"""Map ORM rows to API response models."""

from __future__ import annotations

from app.api.schemas import EventOut, ResultOut, TaskDetailOut, TaskFailureOut, TaskSummaryOut
from app.core.retries import RetryState
from app.core.schemas import ErrorCode, Worker
from app.db.models import Task


def task_summary(task: Task) -> TaskSummaryOut:
    return TaskSummaryOut(
        id=task.id,
        status=task.status.value,
        data_class=task.data_class.value,
        attempt=task.attempt,
        requires_approval=task.requires_approval,
        approved_at=task.approved_at,
        deadline_at=task.deadline_at,
        lane=Worker(task.lane) if task.lane is not None else None,
        next_attempt_at=task.next_attempt_at,
        retry_state=RetryState.model_validate(task.retry_state) if task.retry_state else None,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def failed_task_summary(task: Task) -> TaskFailureOut:
    """Expose only a validated error code, never provider error messages or output."""
    result = max(task.results, key=lambda row: row.attempt, default=None)
    error = result.payload.get("error") if result is not None else None
    code: ErrorCode | None = None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        try:
            code = ErrorCode(error["code"])
        except ValueError:
            pass
    return TaskFailureOut(**task_summary(task).model_dump(), last_error_code=code)


def task_detail(task: Task) -> TaskDetailOut:
    events = sorted(task.events, key=lambda event: event.created_at)
    results = sorted(task.results, key=lambda result: result.attempt)
    return TaskDetailOut(
        **task_summary(task).model_dump(),
        payload=task.payload,
        events=[
            EventOut(
                id=event.id,
                event_type=event.event_type,
                from_status=event.from_status.value if event.from_status else None,
                to_status=event.to_status.value,
                detail=event.detail,
                created_at=event.created_at,
            )
            for event in events
        ],
        results=[
            ResultOut(
                id=result.id,
                attempt=result.attempt,
                payload=result.payload,
                created_at=result.created_at,
            )
            for result in results
        ],
    )
