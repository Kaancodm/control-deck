"""Explicit start and committed stop decisions for lane-bound attempts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.schemas import Worker
from app.db.models import Event, Lease, Task
from app.db.queue import dequeue_next_queued_task
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel
from app.db.worker_ops import (
    WorkerOperationError,
    acknowledge_worker_cancel,
    control_attempt,
    start_worker_attempt,
)
from app.workers.contracts import ControlAction
from tests.integration.test_database import _insert_task

_HASH = "a" * 64


def _prepared_claim(session: Session, *, prepared: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    task = _insert_task(session)
    task_id = task.id
    session.commit()
    claimed = dequeue_next_queued_task(
        session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.id == task_id and claimed.lease is not None
    lease_id = claimed.lease.lease_id
    if prepared:
        _record_preparation(session, claimed, lease_id)
    session.commit()
    return task_id, lease_id


def _record_preparation(
    session: Session,
    task: Task,
    lease_id: uuid.UUID,
    *,
    applied: bool = False,
    placeholders: int = 0,
) -> None:
    session.add(
        Event(
            task_id=task.id,
            event_type="task.prepare",
            actor="system",
            attempt=task.attempt,
            lease_id=lease_id,
            from_status=task.status,
            to_status=task.status,
            detail={
                "lane": task.lane,
                "attempt": task.attempt,
                "lease_id": str(lease_id),
                "payload_sha256": _HASH,
                "redaction_applied": applied,
                "redaction_placeholders": placeholders,
                "artifact_path": f"{task.id}/{task.attempt}/payload.sent.md",
            },
        )
    )
    session.flush()


def _control(session: Session, task_id: uuid.UUID, lease_id: uuid.UUID, **kwargs: object):
    arguments = {"attempt": 1, "worker": Worker.OLLAMA, **kwargs}
    return control_attempt(session, task_id=task_id, lease_id=lease_id, **arguments)


def _start(session: Session, task_id: uuid.UUID, lease_id: uuid.UUID):
    return start_worker_attempt(
        session, task_id=task_id, lease_id=lease_id, attempt=1, worker=Worker.OLLAMA
    )


def test_prepared_attempt_requires_explicit_start_once(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    assert db_session.get(Task, task_id).status == TaskState.LEASED
    assert _start(db_session, task_id, lease_id).action == ControlAction.CONTINUE
    db_session.commit()
    assert db_session.get(Task, task_id).status == TaskState.RUNNING
    with pytest.raises(WorkerOperationError, match="already started"):
        _start(db_session, task_id, lease_id)
    db_session.rollback()
    assert (
        db_session.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "task.start")
        )
        == 1
    )


def test_missing_preparation_cannot_start(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session, prepared=False)
    with pytest.raises(WorkerOperationError, match="prepared payload"):
        _start(db_session, task_id, lease_id)
    db_session.rollback()
    assert db_session.get(Task, task_id).status == TaskState.LEASED


@pytest.mark.parametrize("identity", [{"worker": Worker.CLAUDE}, {"attempt": 2}])
def test_wrong_attempt_identity_never_extends_lease(db_session: Session, identity: dict) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    old_expiry = db_session.get(Lease, lease_id).expires_at
    decision = _control(db_session, task_id, lease_id, extend_by=timedelta(minutes=10), **identity)
    assert decision.action == ControlAction.LOST_LEASE
    db_session.commit()
    assert db_session.get(Lease, lease_id).expires_at == old_expiry


def test_wrong_lease_is_lost_even_if_task_deadline_has_elapsed(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    task = db_session.get(Task, task_id)
    task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert _control(db_session, task_id, uuid.uuid4()).action == ControlAction.LOST_LEASE
    db_session.commit()
    assert db_session.get(Task, task_id).status == TaskState.LEASED
    assert db_session.get(Lease, lease_id) is not None


def test_elapsed_owner_deadline_is_committed_before_stop(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    task = db_session.get(Task, task_id)
    task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    decision = _control(db_session, task_id, lease_id, extend_by=timedelta(minutes=5))
    assert decision.action == ControlAction.EXPIRED
    db_session.commit()
    with Session(db_session.get_bind()) as observer:
        assert observer.get(Task, task_id).status == TaskState.EXPIRED
        assert observer.get(Lease, lease_id) is None
        assert (
            observer.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "task.expire")
            )
            == 1
        )
    assert _control(db_session, task_id, lease_id).action == ControlAction.EXPIRED


def test_lease_timeout_never_creates_task_expiry(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    lease = db_session.get(Lease, lease_id)
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert _control(db_session, task_id, lease_id).action == ControlAction.LOST_LEASE
    db_session.commit()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.LEASED
    assert task.deadline_at is None


@pytest.mark.parametrize("started", [False, True])
def test_requested_cancel_is_acknowledged_before_or_after_start(
    db_session: Session, started: bool
) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    if started:
        _start(db_session, task_id, lease_id)
        db_session.commit()
    request_operator_cancel(db_session, task=db_session.get(Task, task_id), reason="test")
    db_session.commit()
    assert _control(db_session, task_id, lease_id).action == ControlAction.CANCEL
    assert (
        acknowledge_worker_cancel(
            db_session, task_id=task_id, lease_id=lease_id, attempt=1, worker=Worker.OLLAMA
        ).action
        == ControlAction.CANCEL
    )
    db_session.commit()
    assert db_session.get(Task, task_id).status == TaskState.CANCELLED
    assert db_session.get(Lease, lease_id) is None


def test_heartbeat_continues_with_current_database_time(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    old_expiry = db_session.get(Lease, lease_id).expires_at
    decision = _control(db_session, task_id, lease_id, extend_by=timedelta(minutes=10))
    assert decision.action == ControlAction.CONTINUE
    assert decision.expires_at > old_expiry
    assert decision.heartbeat_at is not None
    db_session.commit()


@pytest.mark.parametrize("seconds", [0, -1, 7201])
def test_invalid_heartbeat_extension_is_rejected_without_mutation(
    db_session: Session, seconds: int
) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    old_expiry = db_session.get(Lease, lease_id).expires_at
    with pytest.raises(WorkerOperationError, match="extension"):
        _control(db_session, task_id, lease_id, extend_by=timedelta(seconds=seconds))
    db_session.rollback()
    assert db_session.get(Lease, lease_id).expires_at == old_expiry
