"""Channel acceptance is not worker start, and a vanished runner leaves one result."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.channels.receipt import MissingChannel, accept_task_receipt, settle_due_receipts
from app.core.retries import RetryState, canonical_chain, decide_failure
from app.core.schemas import ErrorCode, TaskV1, Worker
from app.db.models import ChannelReceipt, Event, Result, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.routing import retry_state_for_task
from app.db.state_machine import TaskState
from app.db.transitions import LeaseOwnershipError, request_operator_cancel
from app.db.worker_ops import WorkerOperationError, submit_worker_result
from app.ui.progress import channel_label
from tests.integration.test_database import _insert_task
from tests.integration.test_worker_control import _prepared_claim, _start
from tests.integration.test_worker_identity import _valid_result


class _Desk:
    name = "desk"

    def __init__(self, *, delivered: bool) -> None:
        self.delivered = delivered
        self.calls = 0

    def deliver(self, task_id: uuid.UUID) -> bool:
        self.calls += 1
        return self.delivered


class _MissingNamed(MissingChannel):
    def deliver(self, task_id: uuid.UUID) -> bool:
        return True


def _expire(session: Session, task: Task) -> uuid.UUID:
    assert task.lease is not None
    lease_id = task.lease.lease_id
    task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
    session.commit()
    return lease_id


def _results(session: Session, task_id: uuid.UUID) -> list[Result]:
    return list(
        session.scalars(
            select(Result).where(Result.task_id == task_id).order_by(Result.attempt)
        ).all()
    )


def test_receipt_is_idempotent_and_missing_transport_never_delivers(db_session: Session) -> None:
    task = _insert_task(db_session, approved=False)
    db_session.commit()
    moment = datetime(2026, 11, 8, 4, 0, tzinfo=UTC)
    first = accept_task_receipt(db_session, task, now=moment)
    db_session.commit()
    assert first.status == "accepted"
    assert first.transport == "missing"
    assert first.delivered_at is None
    assert first.deadline_at == moment + timedelta(seconds=60)
    assert channel_label(status=first.status, transport=first.transport) == (
        "Angenommen, Transport fehlt"
    )
    lying = _MissingNamed()
    again = accept_task_receipt(
        db_session, task, now=moment + timedelta(hours=1), adapter=lying, idempotency_key="other-1"
    )
    assert again.id == first.id
    assert again.status == "accepted" and again.transport == "missing"
    assert settle_due_receipts(db_session, now=moment + timedelta(seconds=59)) == 0
    assert settle_due_receipts(db_session, now=moment + timedelta(seconds=60)) == 1
    db_session.refresh(first)
    assert first.status == "deadline_elapsed" and first.delivered_at is None
    assert channel_label(status=first.status, transport=first.transport) == "Frist ohne Zustellung"
    kept = accept_task_receipt(db_session, task, now=moment + timedelta(hours=2))
    assert kept.status == "deadline_elapsed"


def test_named_adapter_delivers_once_and_a_negative_report_stays_open(db_session: Session) -> None:
    moment = datetime(2026, 11, 8, 4, 0, tzinfo=UTC)
    delivered_task = _insert_task(db_session, approved=False)
    quiet_task = _insert_task(db_session, approved=False)
    db_session.commit()
    yes = _Desk(delivered=True)
    delivered = accept_task_receipt(db_session, delivered_task, now=moment, adapter=yes)
    no = _Desk(delivered=False)
    quiet = accept_task_receipt(
        db_session, quiet_task, now=moment, adapter=no, idempotency_key="quiet-1"
    )
    db_session.commit()
    assert delivered.status == "delivered" and delivered.transport == "desk"
    assert delivered.delivered_at == moment
    assert yes.calls == 1
    accept_task_receipt(db_session, delivered_task, now=moment, adapter=yes)
    assert yes.calls == 1
    assert settle_due_receipts(db_session, now=moment + timedelta(seconds=60)) == 1
    db_session.refresh(delivered)
    db_session.refresh(quiet)
    assert delivered.status == "delivered"
    assert quiet.status == "deadline_elapsed" and quiet.delivered_at is None
    other = _insert_task(db_session, approved=False)
    db_session.commit()
    with pytest.raises(ValueError, match="already in use"):
        accept_task_receipt(db_session, other, now=moment, idempotency_key="quiet-1")


def test_approve_records_one_missing_receipt(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    rows = list(db_session.scalars(select(ChannelReceipt).where(ChannelReceipt.task_id == task.id)))
    assert len(rows) == 1
    assert rows[0].transport == "missing" and rows[0].status == "accepted"
    assert task.status == TaskState.QUEUED


def test_external_kill_records_one_result_and_rejects_a_later_result(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    task = db_session.get(Task, task_id)
    assert task is not None and task.status == TaskState.RUNNING
    pinned = retry_state_for_task(task)
    with pytest.raises(LeaseOwnershipError, match="ownership is no longer active"):
        task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
        db_session.flush()
        late = _valid_result(task_id)
        late["status"] = "succeeded"
        submit_worker_result(db_session, lease_id=lease_id, result_payload=late)
    db_session.rollback()
    task = db_session.get(Task, task_id)
    assert task is not None
    _expire(db_session, task)
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 1
    assert retry_state_for_task(task) == pinned
    stored = _results(db_session, task_id)
    assert len(stored) == 1 and stored[0].attempt == 1
    assert stored[0].execution_profile == "unknown"
    assert stored[0].payload["error"]["code"] == "worker_unavailable"
    assert stored[0].payload["error"]["code"] != "timeout"
    assert "timeout" not in stored[0].payload["error"]["message"]
    event = db_session.scalar(
        select(Event).where(Event.task_id == task_id, Event.event_type == "task.lease_expired")
    )
    assert event is not None and event.detail is not None
    assert event.detail["termination"] == "process_lost"
    assert event.detail["error_code"] == "worker_unavailable"
    assert reap_expired_leases(db_session) == 0
    assert len(_results(db_session, task_id)) == 1
    with pytest.raises(WorkerOperationError):
        submit_worker_result(db_session, lease_id=lease_id, result_payload=_valid_result(task_id))
    db_session.rollback()
    assert len(_results(db_session, task_id)) == 1
    recovered = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5)
    )
    assert recovered is not None and recovered.id == task_id and recovered.attempt == 2
    assert recovered.lease is not None and recovered.lease.lease_id != lease_id
    db_session.commit()
    with pytest.raises(WorkerOperationError):
        submit_worker_result(db_session, lease_id=lease_id, result_payload=_valid_result(task_id))
    db_session.rollback()
    assert [row.attempt for row in _results(db_session, task_id)] == [1]


def test_exhausted_kill_fails_once_and_cancel_is_not_a_timeout(db_session: Session) -> None:
    exhausted = _insert_task(db_session, max_attempts=1)
    db_session.commit()
    claimed = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.id == exhausted.id
    _expire(db_session, claimed)
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    db_session.refresh(exhausted)
    assert exhausted.status == TaskState.FAILED
    stored = _results(db_session, exhausted.id)
    assert len(stored) == 1
    assert stored[0].payload["status"] == "failed"
    assert stored[0].payload["error"]["code"] == "worker_unavailable"
    assert (
        dequeue_next_queued_task(db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5))
        is None
    )

    cancelled_id, cancel_lease = _prepared_claim(db_session)
    _start(db_session, cancelled_id, cancel_lease)
    db_session.commit()
    cancelled = db_session.get(Task, cancelled_id)
    assert cancelled is not None
    request_operator_cancel(db_session, task=cancelled, reason="owner")
    _expire(db_session, cancelled)
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    db_session.refresh(cancelled)
    assert cancelled.status == TaskState.CANCELLED
    rows = _results(db_session, cancelled_id)
    assert len(rows) == 1 and rows[0].payload["status"] == "cancelled"
    assert rows[0].payload["error"] is None
    cancel_event = db_session.scalar(
        select(Event).where(Event.task_id == cancelled_id, Event.event_type == "task.cancel")
    )
    assert cancel_event is not None and cancel_event.detail is not None
    assert cancel_event.detail["termination"] == "cancelled"
    assert cancel_event.actor == "system"
    with pytest.raises(WorkerOperationError):
        submit_worker_result(
            db_session, lease_id=cancel_lease, result_payload=_valid_result(cancelled_id)
        )


def test_owner_deadline_does_not_invent_a_runner_result(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    claimed = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.id == task.id
    claimed.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    assert claimed.lease is not None
    claimed.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=5)
    db_session.commit()
    reap_expired_leases(db_session)
    db_session.commit()
    db_session.refresh(claimed)
    assert claimed.status == TaskState.EXPIRED
    assert _results(db_session, claimed.id) == []
    assert not any(event.event_type == "task.lease_expired" for event in claimed.events)


def test_timeout_fallback_stays_distinct_from_process_loss(db_session: Session) -> None:
    payload = TaskV1.model_validate(
        {
            "schema_version": "task.v1",
            "task_id": str(uuid.uuid4()),
            "parent_task_id": None,
            "created_at": "2026-11-08T00:00:00+00:00",
            "requested_by": "system",
            "kind": "custom",
            "title": "policy contrast",
            "instructions": "synthetic",
            "inputs": [],
            "context_refs": [],
            "data_class": "cloud_ok",
            "policy": {"forbidden_workers": [], "max_attempts": 3},
            "acceptance": [],
            "fallback_chain": ["ollama", "claude"],
            "labels": {},
        }
    )
    chain = canonical_chain(payload)
    lost = decide_failure(
        error_code=ErrorCode.WORKER_UNAVAILABLE,
        worker=Worker.OLLAMA,
        attempt=1,
        max_attempts=3,
        chain=chain,
        state=RetryState(cursor=0, pinned_worker=Worker.OLLAMA),
    )
    timed_out = decide_failure(
        error_code=ErrorCode.TIMEOUT,
        worker=Worker.OLLAMA,
        attempt=1,
        max_attempts=3,
        chain=chain,
        state=RetryState(cursor=0, pinned_worker=Worker.OLLAMA),
    )
    assert lost.action == "fallback" and lost.reason == "worker_unavailable"
    assert timed_out.action == "fallback" and timed_out.reason == "timeout"
    assert lost.state.cursor == timed_out.state.cursor == 1
    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    task = db_session.get(Task, task_id)
    assert task is not None
    _expire(db_session, task)
    reap_expired_leases(db_session)
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED
    assert retry_state_for_task(task).pinned_worker == Worker.OLLAMA
    assert (
        db_session.scalar(select(func.count()).select_from(Result).where(Result.task_id == task_id))
        == 1
    )
    assert lease_id is not None
