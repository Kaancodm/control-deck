"""Read-only owner views of terminal failures; no automatic task revival."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.schemas import DataClass
from app.db.models import Task
from app.db.state_machine import TaskState


def list_failed_tasks(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    data_class: DataClass | None = None,
) -> tuple[list[Task], int]:
    """Page failed tasks without locks, deadline sweeps, retries or other writes."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Invalid failure page limit.")
    if type(offset) is not int or offset < 0:
        raise ValueError("Invalid failure page offset.")
    if data_class is not None and not isinstance(data_class, DataClass):
        raise ValueError("Invalid failure data class.")
    filters = [Task.status == TaskState.FAILED]
    if data_class is not None:
        filters.append(Task.data_class == data_class)
    total = session.scalar(select(func.count()).select_from(Task).where(*filters)) or 0
    tasks = session.scalars(
        select(Task)
        .where(*filters)
        .order_by(Task.updated_at.desc(), Task.id)
        .options(selectinload(Task.results))
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    ).all()
    return list(tasks), total
