"""Unit tests for transition helpers (retry limit, trigger resolution)."""

from __future__ import annotations

import uuid

from app.db.models import Task
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import (
    _resolve_requeue_target,
    max_attempts_for_task,
    resolve_trigger,
)


def _task_with_policy(max_attempts: int, attempt: int) -> Task:
    task_id = uuid.uuid4()
    return Task(
        id=task_id,
        payload={
            "schema_version": "task.v1",
            "task_id": str(task_id),
            "policy": {"forbidden_workers": [], "max_attempts": max_attempts},
            "data_class": "local_only",
        },
        attempt=attempt,
    )


def test_resolve_trigger_retry_becomes_fail_when_attempts_exhausted() -> None:
    task = _task_with_policy(max_attempts=3, attempt=3)
    assert resolve_trigger(task, TaskTransition.RETRY) == TaskTransition.FAIL


def test_resolve_trigger_retry_allowed_when_under_limit() -> None:
    task = _task_with_policy(max_attempts=3, attempt=2)
    assert resolve_trigger(task, TaskTransition.RETRY) == TaskTransition.RETRY


def test_max_attempts_default() -> None:
    task_id = uuid.uuid4()
    task = Task(id=task_id, payload={"task_id": str(task_id), "data_class": "local_only"})
    assert max_attempts_for_task(task) == 3


def test_requeue_without_approved_at_goes_to_awaiting_approval() -> None:
    task_id = uuid.uuid4()
    task = Task(
        id=task_id,
        requires_approval=False,
        approved_at=None,
        payload={"task_id": str(task_id), "data_class": "local_only"},
    )
    target = _resolve_requeue_target(
        task,
        TaskTransition.LEASE_EXPIRED,
        TaskState.QUEUED,
    )
    assert target == TaskState.AWAITING_APPROVAL
