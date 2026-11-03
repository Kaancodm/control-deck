"""Owner-facing task CRUD and lifecycle endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, OwnerAuth
from app.api.errors import problem_response
from app.api.schemas import TaskCreateOut, TaskDetailOut, TaskListOut, TaskReviewRequest
from app.api.serializers import task_detail, task_summary
from app.core.schemas import DataClass
from app.db.models import Task
from app.db.queue import expire_overdue_tasks, expire_task_if_due
from app.db.repository import approve_task, create_task_from_api_payload
from app.db.state_machine import InvalidTaskTransitionError, TaskState, TaskTransition
from app.db.transitions import TransitionActor, request_operator_cancel, transition_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=None)
def create_task(
    body: dict[str, Any],
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskCreateOut | JSONResponse:
    """Validate ``task.v1``, persist awaiting approval, and record audit events."""
    try:
        task = create_task_from_api_payload(session, body)
    except IntegrityError:
        session.rollback()
        return problem_response(
            status=409,
            title="Task already exists",
            detail="A task with this task_id is already stored.",
            type_uri="https://control-deck.local/problems/duplicate-task",
        )
    except ValueError as exc:
        issues = exc.args[0] if exc.args else exc
        return problem_response(
            status=422,
            title="Invalid task.v1",
            detail="Task payload failed schema validation.",
            type_uri="https://control-deck.local/problems/invalid-task",
            errors=(
                [{"message": str(item)} for item in issues]
                if isinstance(issues, list)
                else [{"message": str(exc)}]
            ),
        )
    expire_task_if_due(session, task=task)
    session.refresh(task, attribute_names=["events", "results"])
    return TaskCreateOut(task=task_detail(task))


@router.get("", response_model=TaskListOut)
def list_tasks(
    session: DbSession,
    _owner: OwnerAuth,
    status_filter: str | None = Query(default=None, alias="status"),
    data_class: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskListOut:
    """List tasks with optional filters and offset pagination."""
    expire_overdue_tasks(session, limit=200)
    stmt = select(Task).order_by(Task.created_at.desc())
    count_stmt = select(func.count()).select_from(Task)

    if status_filter is not None:
        try:
            TaskState(status_filter)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown status {status_filter!r}",
            ) from exc
        state = TaskState(status_filter)
        stmt = stmt.where(Task.status == state)
        count_stmt = count_stmt.where(Task.status == state)

    if data_class is not None:
        try:
            DataClass(data_class)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown data_class {data_class!r}"
            ) from exc
        stmt = stmt.where(Task.data_class == data_class)
        count_stmt = count_stmt.where(Task.data_class == data_class)

    total = session.scalar(count_stmt) or 0
    rows = session.scalars(stmt.limit(limit).offset(offset)).all()
    return TaskListOut(
        items=[task_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskDetailOut)
def get_task(
    task_id: uuid.UUID,
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskDetailOut:
    """Return a single task including audit events and stored results."""
    task = session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.events), selectinload(Task.results))
        .with_for_update(of=Task)
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    expire_task_if_due(session, task=task)
    session.refresh(task, attribute_names=["events", "results"])
    return task_detail(task)


@router.post("/{task_id}/cancel", response_model=TaskDetailOut)
def cancel_task(
    task_id: uuid.UUID,
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskDetailOut | JSONResponse:
    """Cancel a task (owner override, no lease required)."""
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update(of=Task))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        return problem_response(status=409, title="Task expired", detail="Owner deadline elapsed.")
    try:
        request_operator_cancel(session, task=task, reason="api")
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.refresh(task, attribute_names=["events", "results"])
    return task_detail(task)


@router.post("/{task_id}/approve", response_model=TaskDetailOut)
def approve_task_endpoint(
    task_id: uuid.UUID,
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskDetailOut | JSONResponse:
    """Owner approval: move task from ``awaiting_approval`` into the queue."""
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update(of=Task))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        return problem_response(status=409, title="Task expired", detail="Owner deadline elapsed.")
    try:
        approve_task(session, task)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.refresh(task, attribute_names=["events", "results"])
    return task_detail(task)


@router.post("/{task_id}/review", response_model=TaskDetailOut)
def review_task_endpoint(
    task_id: uuid.UUID,
    body: TaskReviewRequest,
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskDetailOut | JSONResponse:
    """Record the owner's acceptance or rejection of a completed result."""
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update(of=Task))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        return problem_response(status=409, title="Task expired", detail="Owner deadline elapsed.")
    try:
        transition_task(
            session,
            task=task,
            trigger=(
                TaskTransition.REVIEW_APPROVE if body.approved else TaskTransition.REVIEW_REJECT
            ),
            actor=TransitionActor.OPERATOR,
            detail={"approved": body.approved, "reason": body.reason},
        )
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.refresh(task, attribute_names=["events", "results"])
    return task_detail(task)
