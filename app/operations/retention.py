"""Explicit retention of completed task artifacts, preserving operational evidence."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import Base
from app.db.models import Lease, RedactionMap, Task
from app.db.state_machine import TERMINAL_STATES
from app.operations.audit import record_operation
from app.operations.errors import OperationError
from app.operations.files import private_root, remove_tree
from app.operations.models import OperationEvent


@dataclass(frozen=True)
class RetentionSummary:
    candidates: int
    completed: int
    failed: int
    dry_run: bool


def _eligible(cutoff: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        Task.status.in_(TERMINAL_STATES),
        Task.updated_at < cutoff,
        ~exists(select(Lease.lease_id).where(Lease.task_id == Task.id)),
        ~exists(
            select(OperationEvent.id).where(
                OperationEvent.task_id == Task.id,
                OperationEvent.operation_type == "retention",
                OperationEvent.phase == "completed",
            )
        ),
    )


def retain_tasks(
    factory: Callable[[], Session],
    runs_root: Path,
    *,
    apply: bool = False,
    limit: int = 100,
    now: datetime | None = None,
) -> RetentionSummary:
    """Remove only fenced, old terminal artifacts; replay incomplete cleanup safely."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise OperationError("invalid_retention_limit")
    with factory() as session:
        instant = now or session.scalar(select(func.clock_timestamp()))
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise OperationError("invalid_clock")
        cutoff = instant - timedelta(days=90)
        ids = list(
            session.scalars(
                select(Task.id).where(*_eligible(cutoff)).order_by(Task.id).limit(limit)
            )
        )
    if not apply:
        return RetentionSummary(len(ids), 0, 0, True)
    snapshots = Base.metadata.tables.get("task_context_snapshots")
    if snapshots is None:
        raise OperationError("snapshot_schema_unavailable")
    completed = failed = 0
    with private_root(runs_root, create=True) as root:
        for task_id in ids:
            operation_id: UUID = uuid4()
            try:
                # The durable start identifies incomplete work after process death.
                with factory() as session:
                    record_operation(
                        session,
                        operation_id=operation_id,
                        operation_type="retention",
                        phase="started",
                        task_id=task_id,
                    )
                    session.commit()
                with factory() as session:
                    task = session.scalar(
                        select(Task)
                        .where(Task.id == task_id, *_eligible(cutoff))
                        .with_for_update(of=Task, skip_locked=True)
                    )
                    if task is None:
                        raise OperationError("retention_candidate_changed")
                    # Reuse an already quarantined task directory after partial failure.
                    original, pending = str(task_id), ".retention-" + task_id.hex
                    names = os.listdir(root)
                    if original in names and pending in names:
                        raise OperationError("retention_path_conflict")
                    if original in names:
                        os.rename(original, pending, src_dir_fd=root, dst_dir_fd=root)
                        os.fsync(root)
                    files = remove_tree(root, pending) if pending in os.listdir(root) else 0
                    record_operation(
                        session,
                        operation_id=operation_id,
                        operation_type="retention",
                        phase="artifacts_removed",
                        task_id=task_id,
                        counts={"files": files},
                    )
                    maps = int(
                        session.scalar(
                            select(func.count())
                            .select_from(RedactionMap)
                            .where(RedactionMap.task_id == task_id)
                        )
                        or 0
                    )
                    snapshot_count = int(
                        session.scalar(
                            select(func.count())
                            .select_from(snapshots)
                            .where(snapshots.c.task_id == task_id)
                        )
                        or 0
                    )
                    session.execute(delete(RedactionMap).where(RedactionMap.task_id == task_id))
                    session.execute(delete(snapshots).where(snapshots.c.task_id == task_id))
                    record_operation(
                        session,
                        operation_id=operation_id,
                        operation_type="retention",
                        phase="completed",
                        task_id=task_id,
                        counts={"files": files, "maps": maps, "snapshots": snapshot_count},
                    )
                    session.commit()
                    completed += 1
            except Exception:
                failed += 1
                with factory() as session:
                    record_operation(
                        session,
                        operation_id=operation_id,
                        operation_type="retention",
                        phase="failed",
                        task_id=task_id,
                        reason="retention_failed",
                    )
                    session.commit()
    return RetentionSummary(len(ids), completed, failed, False)


def incomplete_operations(session: Session) -> list[UUID]:
    """Expose bounded operation identifiers that have no terminal phase yet."""
    final = select(OperationEvent.operation_id).where(
        OperationEvent.phase.in_(("completed", "failed"))
    )
    return list(
        session.scalars(
            select(OperationEvent.operation_id)
            .where(OperationEvent.phase == "started", ~OperationEvent.operation_id.in_(final))
            .order_by(OperationEvent.id)
            .limit(100)
        )
    )
