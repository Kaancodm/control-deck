"""Lease polling audits unavailable inputs without flooding append-only events."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import Worker
from app.db.models import Event, Task
from app.db.queue import dequeue_next_queued_task
from app.db.repository import record_gate_error
from app.db.session import get_engine
from app.db.state_machine import TaskState
from app.db.transitions import TransitionActor
from app.privacy.gate import PrivacyInputUnavailable
from tests.integration.test_data_gate import _OWNER, _create, _payload
from tests.integration.test_data_gate import gate_client as gate_client
from tests.integration.test_data_gate import privacy_paths as privacy_paths
from tests.integration.test_database import _insert_task


def _gate_events(session: Session, task_id: uuid.UUID) -> list[Event]:
    session.flush()
    return list(
        session.scalars(
            select(Event)
            .where(Event.task_id == task_id, Event.event_type == "task.gate_error")
            .order_by(Event.id)
        )
    )


def test_repeated_errors_in_same_transaction_are_deduplicated(db_session: Session) -> None:
    task = _insert_task(db_session)
    error = PrivacyInputUnavailable("file_unavailable", source="inputs[0].content")

    for _attempt in range(5):
        record_gate_error(db_session, task=task, error=error)

    events = _gate_events(db_session, task.id)
    assert len(events) == 1
    assert events[0].detail == {
        "reason": "privacy_gate",
        "code": "file_unavailable",
        "source": "inputs[0].content",
    }
    assert task.status == TaskState.QUEUED and task.attempt == 0 and task.lease is None


def test_expired_window_allows_one_new_event_using_database_clock(db_session: Session) -> None:
    task = _insert_task(db_session)
    now = db_session.scalar(select(func.clock_timestamp()))
    old = Event(
        task_id=task.id,
        event_type="task.gate_error",
        from_status=task.status,
        to_status=task.status,
        actor=TransitionActor.SYSTEM.value,
        attempt=0,
        detail={"reason": "privacy_gate", "code": "file_unavailable", "source": "task"},
        created_at=now - timedelta(seconds=61),
    )
    db_session.add(old)
    db_session.commit()

    before = db_session.scalar(select(func.clock_timestamp()))
    error = PrivacyInputUnavailable("file_unavailable")
    record_gate_error(db_session, task=task, error=error)
    record_gate_error(db_session, task=task, error=error)
    after = db_session.scalar(select(func.clock_timestamp()))

    events = _gate_events(db_session, task.id)
    assert len(events) == 2
    assert before <= events[-1].created_at <= after


def test_window_is_scoped_to_task_code_and_source(db_session: Session) -> None:
    first = _insert_task(db_session)
    second = _insert_task(db_session)
    variants = [
        ("file_unavailable", "inputs[0].content"),
        ("file_unavailable", "inputs[1].content"),
        ("another_transient_error", "inputs[0].content"),
    ]
    for code, source in variants:
        error = PrivacyInputUnavailable(code, source=source)
        record_gate_error(db_session, task=first, error=error)
        record_gate_error(db_session, task=first, error=error)
    record_gate_error(
        db_session,
        task=second,
        error=PrivacyInputUnavailable("file_unavailable", source="inputs[0].content"),
    )
    assert len(_gate_events(db_session, first.id)) == 3
    assert len(_gate_events(db_session, second.id)) == 1


def test_operator_events_do_not_suppress_system_events(db_session: Session) -> None:
    task = _insert_task(db_session)
    error = PrivacyInputUnavailable("file_unavailable")
    for _attempt in range(2):
        record_gate_error(db_session, task=task, error=error, actor=TransitionActor.OPERATOR)
    db_session.flush()

    record_gate_error(db_session, task=task, error=error)
    record_gate_error(db_session, task=task, error=error)

    events = _gate_events(db_session, task.id)
    assert [event.actor for event in events] == ["operator", "operator", "system"]


def test_repeated_owner_approval_errors_remain_individually_audited(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_text("ordinary input", encoding="utf-8")
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": "input.txt"}]
    _create(gate_client, payload)
    path.unlink()

    for _attempt in range(2):
        response = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)
        assert response.status_code == 409

    task_id = uuid.UUID(payload["task_id"])
    events = _gate_events(db_session, task_id)
    assert len(events) == 2
    assert all(event.actor == "operator" for event in events)
    task = db_session.get(Task, task_id)
    assert task is not None and task.status == TaskState.AWAITING_APPROVAL


def test_concurrent_pollers_and_later_transaction_do_not_duplicate_events(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_text("ordinary input", encoding="utf-8")
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": "input.txt"}]
    _create(gate_client, payload)
    approved = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)
    assert approved.status_code == 200
    path.unlink()
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)

    def poll() -> None:
        with factory() as worker:
            worker.execute(text("SET LOCAL statement_timeout = '2s'"))
            assert (
                dequeue_next_queued_task(
                    worker, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
                )
                is None
            )
            worker.commit()

    with factory() as first:
        assert (
            dequeue_next_queued_task(first, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA)
            is None
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            # The first poll still holds the task lock and its event is uncommitted.
            pool.submit(poll).result(timeout=5)
        first.commit()
    poll()

    events = _gate_events(db_session, uuid.UUID(payload["task_id"]))
    assert len(events) == 1
