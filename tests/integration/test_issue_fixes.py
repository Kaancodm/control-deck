"""Integration tests for review issues #6–#9 and #14."""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import TaskV1, Worker
from app.db.models import Lease, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_api_payload, create_task_from_v1
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


def _task_state_enum_labels(conn) -> list[str]:
    return list(
        conn.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_enum AS e
                JOIN pg_type AS t ON e.enumtypid = t.oid
                WHERE t.typname = 'task_state'
                ORDER BY e.enumsortorder
                """
            )
        ).scalars()
    )


def test_stale_lease_cannot_succeed_after_reaper_and_second_worker(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()

    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker_a = factory()
    worker_b = factory()
    try:
        claimed_a = dequeue_next_queued_task(
            worker_a, lease_ttl=timedelta(seconds=1), worker=Worker.OLLAMA
        )
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
        claimed_b = dequeue_next_queued_task(
            worker_b, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
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
            claimed = dequeue_next_queued_task(
                worker, lease_ttl=timedelta(hours=1), worker=Worker.OLLAMA
            )
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
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
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


@pytest.mark.parametrize("fallback_chain", (["ollama"], ["ollama", "claude"]))
def test_local_only_dequeue_claimable_despite_cloud_fallback_chain(
    db_session: Session,
    fallback_chain: list[str],
) -> None:
    """``local_only`` tasks are pullable on the ollama lane; cloud chain entries are ignored."""
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    payload["data_class"] = "local_only"
    payload["fallback_chain"] = fallback_chain
    task_v1 = TaskV1.model_validate(payload)
    task = create_task_from_v1(db_session, task_v1)
    approve_task(db_session, task)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
        assert claimed is not None
        assert claimed.id == task.id
    finally:
        worker.close()


def test_create_task_from_api_payload_rejects_requires_approval_kwarg(db_session: Session) -> None:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    with pytest.raises(TypeError, match="requires_approval"):
        create_task_from_api_payload(db_session, payload, requires_approval=False)  # type: ignore[call-arg]


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
        assert (
            dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA)
            is None
        )
    finally:
        worker.close()


def test_approve_task_required_before_dequeue(db_session: Session) -> None:
    task = _insert_task(db_session, approved=False)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        assert (
            dequeue_next_queued_task(worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA)
            is None
        )
    finally:
        worker.close()
    approve_task(db_session, task)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
        assert claimed is not None
        assert claimed.id == task.id
    finally:
        worker.close()


def test_expired_lease_heartbeat_rejected(db_session: Session) -> None:
    _insert_task(db_session)
    db_session.commit()
    worker = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)()
    try:
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
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
    setup = factory()
    try:
        claimed = dequeue_next_queued_task(
            setup, lease_ttl=timedelta(seconds=60), worker=Worker.OLLAMA
        )
        assert claimed is not None and claimed.lease is not None
        lease_id = claimed.lease.lease_id
        claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
        setup.commit()
    finally:
        setup.close()

    race_start = threading.Barrier(2)
    thread_errors: list[BaseException] = []

    def run_guarded(fn: Callable[[], None]) -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — propagate after join
            thread_errors.append(exc)

    def heartbeat_worker() -> None:
        session = factory()
        try:
            race_start.wait(timeout=10)
            refresh_lease_heartbeat(
                session,
                lease_id=lease_id,
                task_id=task.id,
                extend_by=timedelta(seconds=60),
            )
            session.commit()
            thread_errors.append(AssertionError("heartbeat must not extend an expired lease"))
        except WorkerOperationError:
            session.rollback()
        finally:
            session.close()

    def reaper_worker() -> None:
        session = factory()
        try:
            race_start.wait(timeout=10)
            reap_expired_leases(session)
            session.commit()
        finally:
            session.close()

    threads = [
        threading.Thread(target=run_guarded, args=(heartbeat_worker,)),
        threading.Thread(target=run_guarded, args=(reaper_worker,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        if thread.is_alive():
            thread_errors.append(AssertionError("concurrency test thread timed out"))

    if thread_errors:
        raise thread_errors[0]

    verify = factory()
    try:
        row = verify.get(Task, task.id)
        assert row is not None
        if row.status != TaskState.QUEUED:
            reap_expired_leases(verify)
            verify.commit()
            verify.refresh(row)
        assert row.status == TaskState.QUEUED
        assert verify.get(Lease, lease_id) is None
    finally:
        verify.close()


def test_unapproved_inflight_requeue_goes_to_awaiting_approval(db_session: Session) -> None:
    """Lease expiry on never-approved work must not land in a stuck ``queued`` row."""
    task_id = uuid.uuid4()
    payload_json = (
        '{"schema_version":"task.v1","task_id":"'
        + str(task_id)
        + '","data_class":"local_only","fallback_chain":["ollama"]}'
    )
    db_session.execute(
        text(
            """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'running', 'local_only', CAST(:payload AS jsonb), 1,
                    true, NULL
                )
                """
        ),
        {"id": task_id, "payload": payload_json},
    )
    db_session.execute(
        text(
            """
            INSERT INTO leases (lease_id, task_id, expires_at, heartbeat_at)
            VALUES (:lease_id, :task_id, :expires_at, :heartbeat_at)
            """
        ),
        {
            "lease_id": uuid.uuid4(),
            "task_id": task_id,
            "expires_at": datetime.now(tz=UTC) - timedelta(seconds=5),
            "heartbeat_at": datetime.now(tz=UTC) - timedelta(seconds=5),
        },
    )
    db_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker = factory()
    try:
        reap_expired_leases(worker)
        worker.commit()
    finally:
        worker.close()
    db_session.refresh(db_session.get(Task, task_id))
    row = db_session.get(Task, task_id)
    assert row is not None
    assert row.status == TaskState.AWAITING_APPROVAL
    assert row.approved_at is None


def test_migration_0002_legacy_running_keeps_approval_pending() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0001")
    engine = get_engine(for_tests=True)
    running_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (id, status, data_class, payload, attempt)
                VALUES (:id, 'running', 'local_only', CAST(:payload AS jsonb), 1)
                """
            ),
            {
                "id": running_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(running_id)
                + '","data_class":"local_only"}',
            },
        )
    command.upgrade(cfg, "20251103_0002")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, approved_at FROM tasks WHERE id = :id"),
            {"id": running_id},
        ).one()
    assert row[0] == "running"
    assert row[1] is None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": running_id})
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


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
    with engine.begin() as conn:
        labels = _task_state_enum_labels(conn)
    assert "awaiting_approval" not in labels
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0004_corrects_old_auto_approve_backfill() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0003")
    engine = get_engine(for_tests=True)
    stale_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'queued', 'local_only', CAST(:payload AS jsonb), 0,
                    false, NOW()
                )
                """
            ),
            {
                "id": stale_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(stale_id)
                + '","data_class":"local_only"}',
            },
        )
    command.upgrade(cfg, "20251103_0004")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": stale_id},
        ).one()
    assert row[0] == "awaiting_approval"
    assert row[1] is True
    assert row[2] is None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": stale_id})
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def _teardown_migration_db(cfg: Config, engine) -> None:
    """Clear migration test rows and reset Alembic revision to head."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def _load_migration_module(filename: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0004_upgrade_sql_idempotent() -> None:
    migration_0004 = _load_migration_module("20251103_0004_approval_gate_correction.py")

    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0003")
    engine = get_engine(for_tests=True)
    stale_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'queued', 'local_only', CAST(:payload AS jsonb), 0,
                    false, NOW()
                )
                """
            ),
            {
                "id": stale_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(stale_id)
                + '","data_class":"local_only"}',
            },
        )
        for statement in (
            migration_0004._CORRECT_QUEUED_WITHOUT_APPROVAL,
            migration_0004._CORRECT_QUEUED_FALSE_REQUIRES_APPROVAL,
            migration_0004._CORRECT_INFLIGHT_FALSE_REQUIRES_APPROVAL,
        ):
            conn.execute(text(statement))
            conn.execute(text(statement))
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": stale_id},
        ).one()
    assert row[0] == "awaiting_approval"
    assert row[1] is True
    assert row[2] is None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": stale_id})
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0004_corrects_inflight_false_requires_approval() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0003")
    engine = get_engine(for_tests=True)
    inflight_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'running', 'local_only', CAST(:payload AS jsonb), 1,
                    false, NOW()
                )
                """
            ),
            {
                "id": inflight_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(inflight_id)
                + '","data_class":"local_only"}',
            },
        )
    command.upgrade(cfg, "20251103_0004")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": inflight_id},
        ).one()
    assert row[0] == "running"
    assert row[1] is True
    assert row[2] is None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": inflight_id})
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0005_revokes_auto_granted_approval_without_event() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0004")
    engine = get_engine(for_tests=True)
    legacy_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'queued', 'local_only', CAST(:payload AS jsonb), 0,
                    true, NOW()
                )
                """
            ),
            {
                "id": legacy_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(legacy_id)
                + '","data_class":"local_only"}',
            },
        )
    command.upgrade(cfg, "20251103_0005")
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


def test_migration_0006_legacy_create_task_v1_requeue_path_from_0003() -> None:
    """
    Old ``create_task_from_v1`` rows: ``requires_approval=false``, ``approved_at=NULL``.

    After upgrading from revision 0003, lease expiry must not return to a dequeueable
    ``queued`` row without owner approval.
    """
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0003")
    engine = get_engine(for_tests=True)
    legacy_id = uuid.uuid4()
    lease_id = uuid.uuid4()
    payload_json = (
        '{"schema_version":"task.v1","task_id":"'
        + str(legacy_id)
        + '","data_class":"local_only","fallback_chain":["ollama"]}'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'running', 'local_only', CAST(:payload AS jsonb), 1,
                    false, NULL
                )
                """
            ),
            {"id": legacy_id, "payload": payload_json},
        )
        conn.execute(
            text(
                """
                INSERT INTO leases (lease_id, task_id, expires_at, heartbeat_at)
                VALUES (:lease_id, :task_id, :expires_at, :heartbeat_at)
                """
            ),
            {
                "lease_id": lease_id,
                "task_id": legacy_id,
                "expires_at": datetime.now(tz=UTC) - timedelta(seconds=5),
                "heartbeat_at": datetime.now(tz=UTC) - timedelta(seconds=5),
            },
        )
    command.upgrade(cfg, "head")
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker = factory()
    try:
        reap_expired_leases(worker)
        worker.commit()
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
        assert claimed is None
    finally:
        worker.close()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": legacy_id},
        ).one()
    assert row[0] == "awaiting_approval"
    assert row[1] is True
    assert row[2] is None
    _teardown_migration_db(cfg, engine)


def test_migration_0006_preserves_owner_approved_tasks_from_0003() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0003")
    engine = get_engine(for_tests=True)
    approved_id = uuid.uuid4()
    payload_json = json.dumps(_minimal_task_payload(approved_id))
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'queued', 'local_only', CAST(:payload AS jsonb), 0,
                    true, NOW()
                )
                """
            ),
            {"id": approved_id, "payload": payload_json},
        )
        conn.execute(
            text(
                """
                INSERT INTO events (task_id, event_type, from_status, to_status, actor)
                VALUES (:task_id, 'task.approve', 'awaiting_approval', 'queued', 'operator')
                """
            ),
            {"task_id": approved_id},
        )
    command.upgrade(cfg, "head")
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    worker = factory()
    try:
        claimed = dequeue_next_queued_task(
            worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
        )
        assert claimed is not None
        assert claimed.id == approved_id
    finally:
        worker.close()
    _teardown_migration_db(cfg, engine)


def test_migration_0006_upgrade_sql_idempotent() -> None:
    migration_0006 = _load_migration_module("20251103_0006_legacy_unapproved_flag.py")

    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0005")
    engine = get_engine(for_tests=True)
    legacy_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'queued', 'local_only', CAST(:payload AS jsonb), 0,
                    false, NULL
                )
                """
            ),
            {
                "id": legacy_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(legacy_id)
                + '","data_class":"local_only"}',
            },
        )
        for _ in range(2):
            conn.execute(text(migration_0006._REGATE_LEGACY_FALSE_WITHOUT_APPROVE))
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, requires_approval, approved_at FROM tasks WHERE id = :id"),
            {"id": legacy_id},
        ).one()
    assert row[0] == "awaiting_approval"
    assert row[1] is True
    assert row[2] is None
    _teardown_migration_db(cfg, engine)


def test_migration_0002_downgrade_blocked_with_awaiting_approval_rows() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0002")
    engine = get_engine(for_tests=True)
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


def test_migration_0002_downgrade_blocked_by_audit_event_reference() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0002")
    engine = get_engine(for_tests=True)
    task_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt, requires_approval
                )
                VALUES (
                    :id, 'queued', 'local_only',
                    CAST(:payload AS jsonb), 0, true
                )
                """
            ),
            {
                "id": task_id,
                "payload": '{"schema_version":"task.v1","task_id":"'
                + str(task_id)
                + '","data_class":"local_only"}',
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO events (
                    task_id, event_type, from_status, to_status
                )
                VALUES (
                    :task_id, 'task.submit', NULL, 'awaiting_approval'
                )
                """
            ),
            {"task_id": task_id},
        )
    with pytest.raises(RuntimeError, match="awaiting_approval"):
        command.downgrade(cfg, "20251103_0001")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM events WHERE task_id = :id"), {"id": task_id})
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
    command.downgrade(cfg, "20251103_0001")
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_migration_0004_downgrade_blocked_with_awaiting_approval_rows() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "20251103_0004")
    engine = get_engine(for_tests=True)
    pending_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt,
                    requires_approval, approved_at
                )
                VALUES (
                    :id, 'awaiting_approval', 'local_only',
                    CAST(:payload AS jsonb), 0, true, NULL
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
    with pytest.raises(RuntimeError, match="pre-0004"):
        command.downgrade(cfg, "20251103_0003")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": pending_id})
    command.downgrade(cfg, "20251103_0003")
    command.upgrade(cfg, "head")
    reset_engine_for_tests()


def test_alembic_rejects_dual_database_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_DATABASE_URL", "postgresql+psycopg://localhost/prod")
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", "postgresql+psycopg://localhost/controldeck_test")
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    with pytest.raises(RuntimeError, match="Both DECK_DATABASE_URL"):
        command.upgrade(cfg, "head")
