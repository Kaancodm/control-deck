"""Channel acceptance is not worker start, and a vanished runner leaves one result."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.channels.receipt import MissingChannel, accept_task_receipt, settle_due_receipts
from app.core.retries import RetryState, canonical_chain, decide_failure
from app.core.schemas import ErrorCode, TaskV1, Worker
from app.db.models import ChannelReceipt, Event, LaneStatus, Result, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.routing import retry_state_for_task
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.db.transitions import LeaseOwnershipError, request_operator_cancel
from app.db.worker_ops import WorkerOperationError, start_worker_attempt, submit_worker_result
from app.ui.progress import channel_label
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_database import _insert_task, _minimal_task_payload
from tests.integration.test_worker_control import _prepared_claim, _record_preparation, _start
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


def test_approval_records_acceptance_and_worker_start_leaves_it(db_session: Session) -> None:
    task = _insert_task(db_session, approved=False)
    db_session.commit()
    assert task.status == TaskState.AWAITING_APPROVAL
    assert (
        db_session.scalar(select(ChannelReceipt).where(ChannelReceipt.task_id == task.id)) is None
    )
    approve_task(db_session, task)
    db_session.commit()
    receipt = db_session.scalar(select(ChannelReceipt).where(ChannelReceipt.task_id == task.id))
    assert receipt is not None
    assert receipt.status == "accepted" and receipt.transport == "missing"
    assert receipt.delivered_at is None
    deadline = receipt.deadline_at
    claimed = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.id == task.id and claimed.lease is not None
    _record_preparation(db_session, claimed, claimed.lease.lease_id)
    _start(db_session, claimed.id, claimed.lease.lease_id)
    db_session.commit()
    db_session.refresh(receipt)
    assert receipt.deadline_at == deadline
    assert receipt.status == "accepted" and receipt.transport == "missing"
    with pytest.raises(ValueError, match="not awaiting approval"):
        approve_task(db_session, task)


def _age_receipt(session: Session, task_id: uuid.UUID) -> None:
    session.execute(
        text(
            "UPDATE channel_receipts SET accepted_at = stamp.moment, "
            "deadline_at = stamp.moment + interval '60 seconds' "
            "FROM (SELECT clock_timestamp() - interval '61 seconds' AS moment) AS stamp "
            "WHERE task_id = :task_id"
        ),
        {"task_id": task_id},
    )


def test_runner_tick_settles_due_receipts_within_the_sweep_limit(
    db_session: Session, tmp_path: Path
) -> None:
    due_one = _insert_task(db_session)
    due_two = _insert_task(db_session)
    waiting = _insert_task(db_session)
    db_session.commit()
    _age_receipt(db_session, due_one.id)
    _age_receipt(db_session, due_two.id)
    update_settings(db_session, {"lanes.ollama.paused": True})
    db_session.commit()
    assert reap_expired_leases(db_session, limit=1) == 0
    db_session.commit()
    statuses = {row.task_id: row.status for row in db_session.scalars(select(ChannelReceipt)).all()}
    assert statuses[waiting.id] == "accepted"
    assert sorted(status for task_id, status in statuses.items() if task_id != waiting.id) == [
        "accepted",
        "deadline_elapsed",
    ]
    assert all(row.delivered_at is None for row in db_session.scalars(select(ChannelReceipt)))
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    outcome = HostRunner(factory, runs_root=tmp_path / "runs").run_once(
        Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA)
    )
    assert outcome.action == "idle"
    db_session.expire_all()
    refreshed = {
        row.task_id: (row.status, row.transport, row.delivered_at)
        for row in db_session.scalars(select(ChannelReceipt)).all()
    }
    assert refreshed[due_one.id][0] == refreshed[due_two.id][0] == "deadline_elapsed"
    assert refreshed[waiting.id] == ("accepted", "missing", None)
    assert refreshed[due_one.id][1] == "missing" and refreshed[due_one.id][2] is None


def test_lost_runner_stays_on_the_pinned_lane_until_max_attempts(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    payload = _minimal_task_payload(uuid.uuid4(), max_attempts=2)
    payload["data_class"] = "cloud_ok"
    payload["fallback_chain"] = ["claude", "codex"]
    task = create_task_from_v1(db_session, TaskV1.model_validate(payload))
    approve_task(db_session, task)
    db_session.commit()
    for expected_attempt in (1, 2):
        claimed = dequeue_next_queued_task(
            db_session, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5)
        )
        assert claimed is not None and claimed.id == task.id and claimed.attempt == expected_attempt
        _expire(db_session, claimed)
        assert reap_expired_leases(db_session) == 1
        db_session.commit()
        db_session.refresh(task)
        assert retry_state_for_task(task).pinned_worker == Worker.CLAUDE
        assert retry_state_for_task(task).cursor == 0
        assert (
            dequeue_next_queued_task(
                db_session, worker=Worker.CODEX, lease_ttl=timedelta(minutes=5)
            )
            is None
        )
        health = db_session.get(LaneStatus, "claude", populate_existing=True)
        assert health is not None and health.unknown_attempts == expected_attempt
        assert reap_expired_leases(db_session) == 0
        db_session.commit()
        assert db_session.get(LaneStatus, "claude", populate_existing=True).unknown_attempts == (
            expected_attempt
        )
    db_session.refresh(task)
    assert task.status == TaskState.FAILED and task.attempt == 2
    stored = _results(db_session, task.id)
    assert [row.payload["error"]["code"] for row in stored] == [
        "worker_unavailable",
        "worker_unavailable",
    ]
    assert (
        dequeue_next_queued_task(db_session, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5))
        is None
    )

    reported = _minimal_task_payload(uuid.uuid4(), max_attempts=3)
    reported["data_class"] = "cloud_ok"
    reported["fallback_chain"] = ["claude", "codex"]
    live = create_task_from_v1(db_session, TaskV1.model_validate(reported))
    approve_task(db_session, live)
    db_session.commit()
    claimed = dequeue_next_queued_task(
        db_session, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.lease is not None
    _record_preparation(db_session, claimed, claimed.lease.lease_id)
    start_worker_attempt(
        db_session,
        task_id=claimed.id,
        lease_id=claimed.lease.lease_id,
        attempt=claimed.attempt,
        worker=Worker.CLAUDE,
    )
    db_session.commit()
    result = _valid_result(claimed.id)
    result.update(
        worker="claude",
        attempt=1,
        status="failed",
        error={"code": "worker_unavailable", "message": "Synthetic provider failure"},
    )
    submitted = submit_worker_result(
        db_session, lease_id=claimed.lease.lease_id, result_payload=result
    )
    db_session.commit()
    assert submitted.status == TaskState.QUEUED
    assert retry_state_for_task(submitted).cursor == 1
    assert retry_state_for_task(submitted).pinned_worker is None
    assert (
        dequeue_next_queued_task(db_session, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5))
        is None
    )
    nxt = dequeue_next_queued_task(db_session, worker=Worker.CODEX, lease_ttl=timedelta(minutes=5))
    assert nxt is not None and nxt.id == live.id and nxt.attempt == 2
