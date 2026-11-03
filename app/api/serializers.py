"""Map ORM rows to API response models."""

from __future__ import annotations

from app.api.schemas import EventOut, ResultOut, TaskDetailOut, TaskSummaryOut
from app.db.models import Task


def task_summary(task: Task) -> TaskSummaryOut:
    return TaskSummaryOut(
        id=task.id,
        status=task.status.value,
        data_class=task.data_class.value,
        attempt=task.attempt,
        requires_approval=task.requires_approval,
        approved_at=task.approved_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


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
