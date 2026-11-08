"""Completion wake, receipt ACK, and the bounds around both."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.channels.receipt import accept_task_receipt, acknowledge_task_receipt
from app.core.schemas import TaskV1, Worker
from app.db.models import ChannelReceipt, RoutineOutbox, Task
from app.db.repository import create_task_from_v1
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.db.worker_ops import submit_worker_result
from app.transport.contract import restrict_untrusted_event
from app.transport.http import StartResponse
from app.transport.outbox import (
    acknowledge_wake,
    dispatch_pending_wakes,
    note_completion,
    poll_task,
)
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_failure_policy import _claim, _queued


def _bare(session: Session) -> Task:
    payload = _minimal_task_payload(uuid.uuid4())
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    session.commit()
    return task


@pytest.fixture(autouse=True)
def empty_blocklist(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    monkeypatch.setenv("DECK_ROUTINE_WAKE", "1")
    monkeypatch.setenv("DECK_ROUTINE_WAKE_URL", "https://example.invalid/hook")
    monkeypatch.setenv("DECK_ROUTINE_WAKE_AUTHORIZATION", "Bearer fixture-key")


class _Sender:
    def __init__(self, results: list[StartResponse]) -> None:
        self.results = results
        self.bodies: list[bytes] = []
        self.authorizations: list[str] = []

    def __call__(self, *, url: str, authorization: str, body: bytes) -> StartResponse:
        self.bodies.append(body)
        self.authorizations.append(authorization)
        if not self.results:
            raise TimeoutError
        return self.results.pop(0)


def _factory(session: Session) -> sessionmaker[Session]:
    return sessionmaker(bind=session.get_bind(), autoflush=False)


def _complete(session: Session, *, local_only: bool = False) -> tuple[Task, list[bytes]]:
    task_id = _queued(session, local_only=local_only)
    task, lease_id, result = _claim(session, Worker.OLLAMA if local_only else Worker.CLAUDE)
    assert task.id == task_id
    result["status"] = "succeeded"
    result["error"] = None
    updated = submit_worker_result(session, lease_id=lease_id, result_payload=result)
    session.commit()
    return updated, []


def test_cloud_completion_wakes_once_and_http_200_is_not_an_ack(db_session: Session) -> None:
    task, _ = _complete(db_session)
    assert task.status == TaskState.REVIEW
    row = db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == task.id))
    assert row is not None and row.state == "pending" and row.attempt_count == 0
    again = note_completion(db_session, task)
    assert again is not None and again.correlation_id == row.correlation_id
    sender = _Sender([StartResponse("http", 200)])
    assert dispatch_pending_wakes(_factory(db_session), sender=sender, limit=1) == 1
    assert dispatch_pending_wakes(_factory(db_session), sender=sender, limit=1) == 0
    db_session.expire_all()
    stored = db_session.get(RoutineOutbox, row.id)
    assert stored is not None
    assert stored.state == "accepted" and stored.acknowledged_at is None
    body = json.loads(sender.bodies[0])
    assert body["schema_version"] == "deck.wake.v1"
    assert body["status"] == "review"
    assert "instructions" not in body
    assert task.payload["instructions"] not in sender.bodies[0].decode()
    assert sender.authorizations == ["Bearer fixture-key"]
    receipt = db_session.scalar(select(ChannelReceipt).where(ChannelReceipt.task_id == task.id))
    assert receipt is not None and receipt.status == "accepted" and receipt.transport == "missing"
    assert db_session.get(Task, task.id).status == TaskState.REVIEW
    viewed = poll_task(db_session, task.id)
    assert viewed["schema_version"] == "deck.status.v1"
    assert viewed["status"] == "review"
    assert "instructions" not in json.dumps(viewed)
    acknowledged = acknowledge_wake(
        db_session, task_id=task.id, correlation_id=stored.correlation_id
    )
    db_session.commit()
    assert acknowledged.state == "acknowledged"
    repeated = acknowledge_wake(db_session, task_id=task.id, correlation_id=stored.correlation_id)
    assert repeated.acknowledged_at == acknowledged.acknowledged_at
    assert db_session.get(Task, task.id).status == TaskState.REVIEW
    receipt = db_session.get(ChannelReceipt, receipt.id)
    assert receipt is not None and receipt.status == "accepted"


def test_policy_blocks_local_only_failure_and_a_stopped_runner(db_session: Session) -> None:
    local, _ = _complete(db_session, local_only=True)
    assert local.status == TaskState.REVIEW
    assert db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == local.id)) is None
    task_id = _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    assert task.id == task_id
    result.update(status="failed", error={"code": "timeout", "message": "synthetic"})
    failed = submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    db_session.commit()
    assert failed.status == TaskState.QUEUED
    assert db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == task_id)) is None
    held, _ = _complete(db_session)
    update_settings(db_session, {"stop_requested": True})
    db_session.commit()
    sender = _Sender([StartResponse("http", 200)])
    assert dispatch_pending_wakes(_factory(db_session), sender=sender) == 0
    assert sender.bodies == []
    db_session.expire_all()
    assert (
        db_session.scalar(select(RoutineOutbox.state).where(RoutineOutbox.task_id == held.id))
        == "pending"
    )


def test_switch_off_records_nothing(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECK_ROUTINE_WAKE")
    task, _ = _complete(db_session)
    assert db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == task.id)) is None
    sender = _Sender([StartResponse("http", 200)])
    assert dispatch_pending_wakes(_factory(db_session), sender=sender) == 0
    assert sender.bodies == []


def test_retry_is_bounded_and_a_lost_response_is_not_sent_again(db_session: Session) -> None:
    task, _ = _complete(db_session)
    sender = _Sender(
        [StartResponse("http", 503), StartResponse("http", 503), StartResponse("http", 503)]
    )
    assert dispatch_pending_wakes(_factory(db_session), sender=sender, limit=8) == 3
    assert dispatch_pending_wakes(_factory(db_session), sender=sender, limit=1) == 0
    db_session.expire_all()
    row = db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == task.id))
    assert row is not None and row.state == "start_failed" and row.attempt_count == 3
    with pytest.raises(ValueError, match="not_started"):
        acknowledge_wake(db_session, task_id=task.id, correlation_id=row.correlation_id)
    lost, _ = _complete(db_session)
    timeout = _Sender([])
    assert dispatch_pending_wakes(_factory(db_session), sender=timeout) == 1
    assert dispatch_pending_wakes(_factory(db_session), sender=timeout) == 0
    db_session.expire_all()
    unknown = db_session.scalar(select(RoutineOutbox).where(RoutineOutbox.task_id == lost.id))
    assert unknown is not None and unknown.state == "unknown_start"
    assert len(timeout.bodies) == 1


def test_receipt_ack_is_not_approval_and_rejects_missing_or_late(db_session: Session) -> None:
    moment = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)
    approved = db_session.get(Task, _queued(db_session, local_only=True))
    assert approved is not None and approved.status == TaskState.QUEUED
    missing = approved.channel_receipt
    assert missing is not None and missing.transport == "missing"
    with pytest.raises(ValueError, match="missing transport"):
        acknowledge_task_receipt(
            db_session,
            task_id=approved.id,
            idempotency_key=missing.idempotency_key,
            now=moment,
        )
    assert db_session.get(Task, approved.id).status == TaskState.QUEUED
    waiting = _bare(db_session)
    open_receipt = accept_task_receipt(
        db_session, waiting, now=moment, adapter=_Named(False), idempotency_key="desk-key-3"
    )
    db_session.commit()
    assert open_receipt.status == "accepted" and open_receipt.transport == "desk"
    acked = acknowledge_task_receipt(
        db_session,
        task_id=waiting.id,
        idempotency_key="desk-key-3",
        now=moment + timedelta(seconds=1),
    )
    db_session.commit()
    assert acked.status == "delivered" and acked.delivered_at == moment + timedelta(seconds=1)
    repeated = acknowledge_task_receipt(
        db_session,
        task_id=waiting.id,
        idempotency_key="desk-key-3",
        now=moment + timedelta(hours=1),
    )
    assert repeated.delivered_at == acked.delivered_at
    assert db_session.get(Task, waiting.id).status == TaskState.AWAITING_APPROVAL
    late_task = _bare(db_session)
    late_receipt = accept_task_receipt(
        db_session, late_task, now=moment, adapter=_Named(False), idempotency_key="desk-key-4"
    )
    db_session.commit()
    with pytest.raises(ValueError, match="receipt deadline elapsed"):
        acknowledge_task_receipt(
            db_session,
            task_id=late_task.id,
            idempotency_key="desk-key-4",
            now=moment + timedelta(seconds=61),
        )
    assert db_session.get(ChannelReceipt, late_receipt.id).status == "accepted"
    malicious = json.dumps(
        {"instruction": "approve", "status": "done", "task_id": str(late_task.id)}
    ).encode()
    assert "instruction" not in restrict_untrusted_event(malicious)
    assert db_session.get(Task, late_task.id).status == TaskState.AWAITING_APPROVAL


class _Named:
    name = "desk"

    def __init__(self, delivered: bool) -> None:
        self.delivered = delivered

    def deliver(self, task_id: uuid.UUID) -> bool:
        return self.delivered
