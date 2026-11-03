"""Owner actions and read-only views, independent of an HTTP transport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.schemas import DataClass, Worker
from app.db.models import Task
from app.db.queue import expire_task_if_due
from app.db.repository import approve_task, create_task_from_api_payload
from app.db.state_machine import InvalidTaskTransitionError, TaskState, TaskTransition
from app.db.transitions import TransitionActor, request_operator_cancel, transition_task

OwnerAction = Literal["approve", "accept", "reject", "cancel", "unblock"]

# An unblock reverses a data-gate or routing block, so the owner must justify it (stage 11 D8).
MIN_UNBLOCK_REASON_CHARS = 10


class OwnerTaskError(ValueError):
    """A safe transport-independent reason for an unavailable owner action."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Owner task action is unavailable.")


def find_task(session: Session, task_id: UUID, *, lock: bool = False) -> Task:
    statement = (
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.events), selectinload(Task.results))
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=Task)
    task = session.scalar(statement)
    if task is None:
        raise OwnerTaskError("not_found")
    return task


def submit_task(session: Session, payload: dict[str, Any]) -> Task:
    """Submit through the same schema, privacy and routing gates as the API."""
    return create_task_from_api_payload(session, payload)


def act_on_task(
    session: Session, task_id: UUID, action: OwnerAction, *, reason: str | None = None
) -> Task:
    """Apply one explicit owner action; caller commits even an elapsed deadline."""
    if reason is not None and len(reason) > 2000:
        raise OwnerTaskError("invalid_reason")
    task = find_task(session, task_id, lock=True)
    if expire_task_if_due(session, task=task) or task.status == TaskState.EXPIRED:
        raise OwnerTaskError("expired")
    if action == "approve":
        approve_task(session, task)
    elif action == "cancel":
        request_operator_cancel(session, task=task, reason=reason or "owner")
    elif action in {"accept", "reject"}:
        transition_task(
            session,
            task=task,
            trigger=(
                TaskTransition.REVIEW_APPROVE
                if action == "accept"
                else TaskTransition.REVIEW_REJECT
            ),
            actor=TransitionActor.OPERATOR,
            detail={"approved": action == "accept", "reason": reason},
        )
    elif action == "unblock":
        justification = (reason or "").strip()
        if len(justification) < MIN_UNBLOCK_REASON_CHARS:
            raise OwnerTaskError("invalid_reason")
        if task.status != TaskState.BLOCKED:
            raise InvalidTaskTransitionError(
                f"unblock is only allowed from 'blocked', not {task.status.value!r}"
            )
        # The owner's justified release also counts as the start approval a blocked
        # task never received; without it the queue would never claim the task.
        if task.approved_at is None:
            task.approved_at = datetime.now(tz=UTC)
        transition_task(
            session,
            task=task,
            trigger=TaskTransition.UNBLOCK,
            actor=TransitionActor.OWNER,
            detail={"reason": justification},
        )
    else:
        raise OwnerTaskError("invalid_action")
    session.flush()
    session.refresh(task, attribute_names=["events", "results"])
    return task


def list_owner_tasks(
    session: Session,
    *,
    state: TaskState | None = None,
    data_class: DataClass | None = None,
    lane: Worker | None = None,
    needs_owner: bool = False,
    search: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    """Read owner task pages without sweeps, leases, locks or hidden mutations."""
    if not 1 <= limit <= 200 or offset < 0 or len(search) > 120:
        raise OwnerTaskError("invalid_filter")
    predicates = []
    if state is not None:
        predicates.append(Task.status == state)
    if needs_owner:
        predicates.append(Task.status.in_((TaskState.AWAITING_APPROVAL, TaskState.REVIEW)))
    if data_class is not None:
        predicates.append(Task.data_class == data_class)
    if lane is not None:
        predicates.append(Task.lane == lane)
    if search:
        # Search is literal, including the SQL LIKE wildcard characters.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        predicates.append(Task.payload["title"].astext.ilike(f"%{escaped}%", escape="\\"))
    total = session.scalar(select(func.count()).select_from(Task).where(*predicates)) or 0
    rows = session.scalars(
        select(Task)
        .where(*predicates)
        .options(selectinload(Task.results))
        .order_by(Task.created_at.desc(), Task.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total
