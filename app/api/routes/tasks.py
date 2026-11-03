"""Owner-facing task CRUD and lifecycle endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, OwnerAuth
from app.api.errors import problem_response
from app.api.schemas import (
    DryRunFindingOut,
    LaneBudgetOut,
    TaskCreateOut,
    TaskDetailOut,
    TaskDryRunOut,
    TaskFailureListOut,
    TaskListOut,
    TaskReviewRequest,
)
from app.api.serializers import failed_task_summary, task_detail, task_summary
from app.core.schemas import DataClass, TaskV1, Worker
from app.core.validate import validate_task
from app.db.failures import list_failed_tasks
from app.db.models import Task
from app.db.queue import expire_overdue_tasks, expire_task_if_due
from app.db.repository import approve_task, create_task_from_api_payload
from app.db.routing import routing_snapshot
from app.db.state_machine import InvalidTaskTransitionError, TaskState, TaskTransition
from app.db.transitions import TransitionActor, request_operator_cancel, transition_task
from app.privacy.config import PrivacyConfigurationError
from app.privacy.gate import (
    PrivacyInputError,
    PrivacyInputUnavailable,
    PrivacyViolation,
    inspect_task,
)
from app.routing.config import RouterConfigurationError, get_router_config
from app.routing.router import route_task

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
    except RouterConfigurationError:
        session.rollback()
        return _routing_unavailable()
    except PrivacyConfigurationError:
        return problem_response(
            status=503,
            title="Privacy configuration unavailable",
            detail="The local privacy configuration must be available before submitting tasks.",
        )
    except PrivacyInputError as exc:
        details = (
            exc.report.safe_detail()["findings"]
            if isinstance(exc, PrivacyViolation)
            else [{"code": exc.code, "source": exc.source}]
        )
        return problem_response(
            status=422,
            title="Task rejected by privacy gate",
            detail="Task input failed privacy checks; no task was stored.",
            errors=details,
        )
    except IntegrityError:
        session.rollback()
        return problem_response(
            status=409,
            title="Task already exists",
            detail="A task with this task_id is already stored.",
            type_uri="https://control-deck.local/problems/duplicate-task",
        )
    except ValueError:
        return problem_response(
            status=422,
            title="Invalid task.v1",
            detail="Task payload failed schema validation.",
            type_uri="https://control-deck.local/problems/invalid-task",
            # Validator messages may quote values or unknown field names.
            errors=[{"code": "invalid_task", "message": "Check task fields and deadline_at."}],
        )
    session.refresh(task, attribute_names=["events", "results"])
    return TaskCreateOut(task=task_detail(task))


def _routing_unavailable() -> JSONResponse:
    return problem_response(
        status=503,
        title="Routing configuration unavailable",
        detail="The local routing configuration must be available before planning tasks.",
    )


@router.post("/dry-run", response_model=TaskDryRunOut)
def dry_run_task(
    body: dict[str, Any],
    session: DbSession,
    _owner: OwnerAuth,
) -> TaskDryRunOut | JSONResponse:
    """Preview current admission and routing without persisting or executing anything."""
    try:
        data = dict(body)
        raw_deadline = data.pop("deadline_at", None)
        if raw_deadline is not None and not isinstance(raw_deadline, str):
            raise ValueError("Invalid deadline type")
        deadline_at = TypeAdapter(AwareDatetime | None).validate_python(
            datetime.fromisoformat(raw_deadline) if raw_deadline is not None else None
        )
        if validate_task(data):
            raise ValueError("Invalid task payload")
        task = TaskV1.model_validate(data)
        config = get_router_config()
        snapshot = routing_snapshot(session)
        if deadline_at is not None and deadline_at <= snapshot.now:
            raise ValueError("Deadline elapsed")
        gate = inspect_task(task)
        if gate.has_secrets:
            raise PrivacyViolation(gate)
        plan = route_task(
            task, config=config, usage=snapshot.usage, budget_date=snapshot.budget_date
        )
    except RouterConfigurationError:
        return _routing_unavailable()
    except PrivacyConfigurationError:
        return problem_response(
            status=503,
            title="Privacy configuration unavailable",
            detail="The local privacy configuration must be available before planning tasks.",
        )
    except PrivacyInputError as exc:
        return problem_response(
            status=422,
            title="Task rejected by privacy gate",
            detail="Task input failed privacy checks; nothing was stored or executed.",
            errors=[{"code": exc.code, "source": exc.source}],
        )
    except ValueError:
        return problem_response(
            status=422,
            title="Invalid task.v1",
            detail="Task payload failed schema validation.",
            errors=[{"code": "invalid_task", "message": "Check task fields and deadline_at."}],
        )
    budgets: dict[Worker, LaneBudgetOut] = {}
    for worker in Worker:
        limit = config.lanes[worker].daily_attempt_limit
        used = snapshot.usage.get(worker, 0)
        budgets[worker] = LaneBudgetOut(
            used=used,
            limit=limit,
            remaining=max(0, limit - used) if limit is not None else None,
        )
    # This mirrors the current intake gate. It does not claim that a planned
    # redaction-capable lane can already receive a raw task through the legacy API.
    return TaskDryRunOut(
        status="blocked" if gate.blocked else plan.status,
        reason="privacy_gate" if gate.blocked else plan.reason,
        plan=plan,
        gate_status="blocked" if gate.blocked else "clear",
        findings=[
            DryRunFindingOut(
                kind=item.kind.value,
                rule=item.rule,
                source=item.source,
                start=item.start,
                end=item.end,
            )
            for item in gate.findings
        ],
        budgets=budgets,
    )


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


@router.get("/failed", response_model=TaskFailureListOut)
def list_task_failures(
    session: DbSession,
    _owner: OwnerAuth,
    data_class: DataClass | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskFailureListOut:
    """Read terminal failures without reviving tasks or sweeping deadlines."""
    rows, total = list_failed_tasks(session, data_class=data_class, limit=limit, offset=offset)
    return TaskFailureListOut(
        items=[failed_task_summary(row) for row in rows],
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
    except PrivacyInputUnavailable as exc:
        return problem_response(
            status=409,
            title="Task input temporarily unavailable",
            detail="Approval was deferred; restore the attachment and retry.",
            errors=[{"code": exc.code, "source": exc.source}],
        )
    except RouterConfigurationError:
        session.rollback()
        return _routing_unavailable()
    except PrivacyConfigurationError:
        return problem_response(
            status=503,
            title="Privacy configuration unavailable",
            detail="The local privacy configuration must be available before approval.",
        )
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
