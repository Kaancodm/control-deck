"""Integration tests for review issues #6–#9 and #14."""

from __future__ import annotations

import argparse
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import TaskV1
from app.db.models import Lease, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.session import get_engine, reset_engine_for_tests, session_scope
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import (
    LeaseOwnershipError,
    TransitionActor,
    request_operator_cancel,
    transition_task,
)
from app.db.worker_ops import WorkerOperationError, refresh_lease_heartbeat
from tests.integration.helpers import require_test_database_url
from tests.integration.test_database import _insert_task, _minimal_task_payload


def test_stale_lease_cannot_succeed_after_reaper_and_second_worker(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker_a = factory()
    worker_b = factory()
    try:
        claimed_a = dequeue_next_queued_task(worker_a, lease_ttl=timedelta(seconds=1))
        assert claimed_a is not None
        lease_a = claimed_a.lease.lease_id if claimed_a.lease else None
        assert lease_a is not None
        transition_task(
            worker_a,
            task=claimed_a,
            trigger=TaskTransition.START,
            lease_id=lease_a,
            actor=TransitionActor.RUNNER,
        )
        worker_a.commit()
    finally:
        worker_a.close()

    worker_reap = factory()
    try:
        db_task = worker_reap.get(Task, task.id)
        assert db_task is not None and db_task.lease is not None
        db_task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
        worker_reap.commit()
        reap_expired_leases(worker_reap)
        worker_reap.commit()
    finally:
        worker_reap.close()

    try:
        claimed_b = dequeue_next_queued_task(worker_b, lease_ttl=timedelta(seconds=30))
        assert claimed_b is not None
        lease_b = claimed_b.lease.lease_id if claimed_b.lease else None
        assert lease_b is not None
        transition_task(
            worker_b,
            task=claimed_b,
            trigger=TaskTransition.START,
            lease_id=lease_b,
            actor=TransitionActor.RUNNER,
        )
        worker_b.commit()
    finally:
        worker_b.close()

    stale_session = factory()
    try:
        stale_task = stale_session.get(Task, task.id)
        assert stale_task is not None
        with pytest.raises(LeaseOwnershipError):
            transition_task(
                stale_session,
                task=stale_task,
                trigger=TaskTransition.SUCCEED,
                lease_id=lease_a,
                actor=TransitionActor.RUNNER,
            )
    finally:
        stale_session.close()


def test_reaper_respects_max_attempts(db_session: Session) -> None:
    task = _insert_task(db_session, max_attempts=2)
    db_session.commit()

    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    for _ in range(2):
        worker = factory()
        try:
            claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(hours=1))
            assert claimed is not None
            lease_id = claimed.lease.lease_id if claimed.lease else None
            assert lease_id is not None
            claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)  # type: ignore[union-attr]
            worker.commit()
        finally:
            worker.close()
        worker_reap = factory()
        try:
            reap_expired_leases(worker_reap)
            worker_reap.commit()
        finally:
            worker_reap.close()

    db_session.refresh(task)
    assert task.status == TaskState.FAILED
    assert task.attempt == 2


def test_operator_cancel_paths(db_session: Session) -> None:
    queued = _insert_task(db_session)
    db_session.commit()
    request_operator_cancel(db_session, task=queued, reason="test")
    db_session.commit()
    db_session.refresh(queued)
    assert queued.status == TaskState.CANCELLED

    running_id = uuid.uuid4()
    running = _insert_task(db_session, running_id)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        lease_id = claimed.lease.lease_id if claimed.lease else None
        assert lease_id is not None
        transition_task(
            worker,
            task=claimed,
            trigger=TaskTransition.START,
            lease_id=lease_id,
            actor=TransitionActor.RUNNER,
        )
        worker.commit()
    finally:
        worker.close()
    db_session.refresh(running)
    request_operator_cancel(db_session, task=running, reason="stop")
    db_session.commit()
    db_session.refresh(running)
    assert running.cancel_requested is True
    assert running.status == TaskState.RUNNING


def test_payload_immutability_and_event_append_only(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    with pytest.raises((IntegrityError, Exception)):
        db_session.execute(
            text("UPDATE tasks SET payload = payload || CAST(:fragment AS jsonb) WHERE id = :id"),
            {"fragment": '{"x": 1}', "id": task.id},
        )
        db_session.commit()
    db_session.rollback()
    event_id = db_session.scalar(
        text("SELECT id FROM events WHERE task_id = :id LIMIT 1"),
        {"id": task.id},
    )
    assert event_id is not None
    with pytest.raises((IntegrityError, Exception)):
        db_session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event_id})
        db_session.commit()
    db_session.rollback()


def test_create_task_idempotent(db_session: Session) -> None:
    task_id = uuid.uuid4()
    first = _insert_task(db_session, task_id)
    second = _insert_task(db_session, task_id)
    assert first.id == second.id


def test_session_scope_commits_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    test_url = os.environ.get("DECK_TEST_DATABASE_URL", "").strip()
    if not test_url:
        pytest.skip("DECK_TEST_DATABASE_URL is not set")
    monkeypatch.setenv("DECK_DATABASE_URL", test_url)
    reset_engine_for_tests()
    task_id = uuid.uuid4()
    task_v1 = TaskV1.model_validate(_minimal_task_payload(task_id))
    with session_scope() as session:
        create_task_from_v1(session, task_v1)
    with session_scope() as session:
        assert session.get(Task, task_id) is not None


@pytest.mark.xfail(reason="Worker/data_class lane filter planned for Etappe 7/8 (#14)")
def test_dequeue_respects_local_only_lane(db_session: Session) -> None:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    payload["data_class"] = "local_only"
    payload["fallback_chain"] = ["claude"]
    task_v1 = TaskV1.model_validate(payload)
    create_task_from_v1(db_session, task_v1)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is None
    finally:
        worker.close()


def test_create_task_from_v1_starts_awaiting_approval(db_session: Session) -> None:
    task_id = uuid.uuid4()
    task_v1 = TaskV1.model_validate(_minimal_task_payload(task_id))
    task = create_task_from_v1(db_session, task_v1)
    db_session.commit()
    assert task.status == TaskState.AWAITING_APPROVAL
    assert task.approved_at is None
    assert task.requires_approval is True
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        assert dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30)) is None
    finally:
        worker.close()


def test_approve_task_required_before_dequeue(db_session: Session) -> None:
    task = _insert_task(db_session, approved=False)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        assert dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30)) is None
    finally:
        worker.close()
    approve_task(db_session, task)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None
        assert claimed.id == task.id
    finally:
        worker.close()


def test_expired_lease_heartbeat_rejected(db_session: Session) -> None:
    _insert_task(db_session)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30))
        assert claimed is not None and claimed.lease is not None
        lease_id = claimed.lease.lease_id
        claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        worker.commit()
        with pytest.raises(WorkerOperationError, match="expired"):
            refresh_lease_heartbeat(
                worker,
                lease_id=lease_id,
                task_id=claimed.id,
                extend_by=timedelta(seconds=30),
            )
    finally:
        worker.close()


def test_heartbeat_and_reaper_concurrent(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    lease_id_holder: list[uuid.UUID] = []
    barrier = threading.Barrier(2)

    def heartbeat_worker() -> None:
        session = factory()
        try:
            claimed = dequeue_next_queued_task(session, lease_ttl=timedelta(seconds=60))
            assert claimed is not None and claimed.lease is not None
            lease_id_holder.append(claimed.lease.lease_id)
            session.commit()
            barrier.wait(timeout=10)
            claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
            session.commit()
            barrier.wait(timeout=10)
            with pytest.raises(WorkerOperationError):
                refresh_lease_heartbeat(
                    session,
                    lease_id=claimed.lease.lease_id,
                    task_id=claimed.id,
                    extend_by=timedelta(seconds=60),
                )
        finally:
            session.close()

    def reaper_worker() -> None:
        session = factory()
        try:
            barrier.wait(timeout=10)
            reap_expired_leases(session)
            session.commit()
            barrier.wait(timeout=10)
        finally:
            session.close()

    threads = [
        threading.Thread(target=heartbeat_worker),
        threading.Thread(target=reaper_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    verify = factory()
    try:
        row = verify.get(Task, task.id)
        assert row is not None
        assert row.status in (TaskState.QUEUED, TaskState.LEASED)
        assert lease_id_holder
        lease = verify.get(Lease, lease_id_holder[0])
        if lease is not None:
            assert lease.expires_at <= datetime.now(tz=UTC) + timedelta(seconds=5)
    finally:
        verify.close()


def test_migration_0002_legacy_queued_becomes_awaiting_approval() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0001")
    engine = get_engine(for_tests=True)
    legacy_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (id, status, data_class, payload, attempt)
                VALUES (:id, 'queued', 'local_only', CAST(:payload AS jsonb), 0)
                """
            ),
            {
                "id": legacy_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(legacy_id)
                + '","data_class":"local_only"}',
            },
        )
    command.upgrade(cfg, "20251103_0002")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": legacy_id},
        ).one()
    assert row[0] == "awaiting_approval"
    assert row[1] is True
    assert row[2] is None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": legacy_id})
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0002_downgrade_reversible_without_awaiting_approval() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    engine = get_engine(for_tests=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "20251103_0001")
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0002_downgrade_blocked_with_awaiting_approval_rows() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    engine = get_engine(for_tests=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.upgrade(cfg, "head")
    pending_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt, requires_approval
                )
                VALUES (
                    :id, 'awaiting_approval', 'local_only',
                    CAST(:payload AS jsonb), 0, true
                )
                """
            ),
            {
                "id": pending_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(pending_id)
                + '","data_class":"local_only"}',
            },
        )
    with pytest.raises(RuntimeError, match="awaiting_approval"):
        command.downgrade(cfg, "20251103_0001")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": pending_id})
    command.downgrade(cfg, "20251103_0001")
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_alembic_rejects_dual_database_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_DATABASE_URL", "postgresql+psycopg://localhost/prod")
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", "postgresql+psycopg://localhost/controldeck_test")
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    with pytest.raises(RuntimeError, match="Both DECK_DATABASE_URL"):
        command.upgrade(cfg, "head")
