"""Integration tests against PostgreSQL (queue, migrations, SKIP LOCKED)."""

from __future__ import annotations

import argparse
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import DataClass, TaskV1
from app.db.models import Event, Lease, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.session import get_engine, reset_engine_for_tests
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import (
    LeaseOwnershipError,
    fenced_transition_task,
    transition_task,
)
from tests.integration.helpers import require_test_database_url


def _minimal_task_payload(task_id: uuid.UUID, *, max_attempts: int = 3) -> dict[str, object]:
    return {
        "schema_version": "task.v1",
        "task_id": str(task_id),
        "parent_task_id": None,
        "created_at": "2025-11-03T00:00:00+00:00",
        "requested_by": "system",
        "kind": "custom",
        "title": "db-test",
        "instructions": "integration test task",
        "inputs": [],
        "context_refs": [],
        "data_class": "local_only",
        "policy": {"forbidden_workers": [], "max_attempts": max_attempts},
        "acceptance": [],
        "fallback_chain": ["ollama"],
        "labels": {},
    }


def _insert_task(
    session: Session,
    task_id: uuid.UUID | None = None,
    *,
    max_attempts: int = 3,
    approved: bool = True,
) -> Task:
    task_id = task_id or uuid.uuid4()
    task_v1 = TaskV1.model_validate(_minimal_task_payload(task_id, max_attempts=max_attempts))
    task = create_task_from_v1(session, task_v1)
    if approved and task.status == TaskState.AWAITING_APPROVAL:
        approve_task(session, task)
    return task


def test_alembic_upgrade_and_downgrade_roundtrip() -> None:
    """Migrations apply cleanly and downgrade removes the schema."""
    require_test_database_url()
    reset_engine_for_tests()
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    reset_engine_for_tests()


def test_dequeue_skip_locked_two_sessions(db_session: Session) -> None:
    """Two workers dequeue distinct queued tasks under concurrent locks."""
    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    _insert_task(db_session, id_a)
    _insert_task(db_session, id_b)
    db_session.commit()

    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker_a = factory()
    worker_b = factory()
    try:
        first = dequeue_next_queued_task(worker_a, lease_ttl=timedelta(seconds=30))
        second = dequeue_next_queued_task(worker_b, lease_ttl=timedelta(seconds=30))
        assert first is not None
        assert second is not None
        assert first.id != second.id
        assert {first.id, second.id} == {id_a, id_b}
        assert first.status == TaskState.LEASED
        assert second.status == TaskState.LEASED
        worker_a.commit()
        worker_b.commit()
    finally:
        worker_a.close()
        worker_b.close()


def test_dequeue_flush_allows_second_claim_in_same_session(db_session: Session) -> None:
    """After flush, a second dequeue in the same transaction does not re-select the leased row."""
    _insert_task(db_session)
    _insert_task(db_session)
    db_session.commit()

    session = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        first = dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=30))
        second = dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=30))
        assert first is not None
        assert second is not None
        assert first.id != second.id
    finally:
        session.rollback()
        session.close()


def test_dequeue_writes_acquire_audit_event(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        worker.commit()
    finally:
        worker.close()

    count = db_session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.task_id == task.id, Event.event_type == "task.acquire")
    )
    assert count == 1


def test_reacquire_after_lease_expired(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        lease_id = claimed.lease.lease_id if claimed.lease else None
        assert lease_id is not None
        worker.commit()
    finally:
        worker.close()

    worker2 = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        db_task = worker2.get(Task, task.id)
        assert db_task is not None
        assert db_task.lease is not None
        db_task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
        worker2.flush()
        transition_task(
            worker2,
            task=db_task,
            trigger=TaskTransition.LEASE_EXPIRED,
            lease_id=lease_id,
        )
        worker2.commit()
    finally:
        worker2.close()

    worker3 = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        again = dequeue_next_queued_task(worker3, lease_ttl=timedelta(seconds=30))
        assert again is not None
        assert again.id == task.id
        assert again.status == TaskState.LEASED
        assert again.attempt == 2
        worker3.commit()
    finally:
        worker3.close()

    lease_rows = db_session.scalar(select(func.count()).select_from(Lease))
    assert lease_rows == 1


def test_reaper_returns_expired_leased_task_to_queue(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(hours=1))
        assert claimed is not None
        assert claimed.lease is not None
        claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
        worker.commit()
    finally:
        worker.close()

    worker2 = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        reaped = reap_expired_leases(worker2)
        assert reaped == 1
        worker2.commit()
    finally:
        worker2.close()

    db_session.refresh(task)
    assert task.status == TaskState.QUEUED
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def test_retry_limit_moves_to_failed(db_session: Session) -> None:
    task = _insert_task(db_session, max_attempts=1)
    db_session.commit()

    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        lease_id = claimed.lease.lease_id if claimed.lease else None
        assert lease_id is not None
        transition_task(worker, task=claimed, trigger=TaskTransition.START, lease_id=lease_id)
        transition_task(worker, task=claimed, trigger=TaskTransition.RETRY, lease_id=lease_id)
        worker.commit()
    finally:
        worker.close()

    db_session.refresh(task)
    assert task.status == TaskState.FAILED
    assert task.attempt == 1


def test_fencing_rejects_stale_lease_id(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        real_lease = claimed.lease.lease_id if claimed.lease else None
        assert real_lease is not None
        worker.commit()
    finally:
        worker.close()

    stale = uuid.uuid4()
    worker2 = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        db_task = worker2.get(Task, task.id)
        assert db_task is not None
        with pytest.raises(LeaseOwnershipError):
            transition_task(
                worker2,
                task=db_task,
                trigger=TaskTransition.START,
                lease_id=stale,
            )
        assert (
            fenced_transition_task(
                worker2,
                task_id=task.id,
                from_state=TaskState.LEASED,
                lease_id=stale,
                trigger=TaskTransition.START,
            )
            is None
        )
    finally:
        worker2.rollback()
        worker2.close()


def test_parallel_dequeue_threads(db_session: Session) -> None:
    task_ids = [uuid.uuid4() for _ in range(4)]
    for task_id in task_ids:
        _insert_task(db_session, task_id)
    db_session.commit()

    claimed_ids: list[uuid.UUID] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def worker_fn() -> None:
        session = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
        try:
            barrier.wait(timeout=10)
            task = dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=30))
            if task is not None:
                with lock:
                    claimed_ids.append(task.id)
            session.commit()
        finally:
            session.close()

    threads = [threading.Thread(target=worker_fn) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(claimed_ids) == 4
    assert set(claimed_ids) == set(task_ids)


def test_create_task_persists_explicit_data_class(db_session: Session) -> None:
    task_id = uuid.uuid4()
    task_v1 = TaskV1.model_validate(_minimal_task_payload(task_id))
    task = create_task_from_v1(db_session, task_v1)
    db_session.flush()
    db_session.refresh(task)
    assert task.data_class == DataClass.LOCAL_ONLY
