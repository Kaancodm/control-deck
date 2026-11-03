"""Results remain bound to prepared lane, attempt and exact transport bytes."""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Result, Task
from app.db.queue import dequeue_next_queued_task
from app.db.repository import approve_task, create_task_from_v1
from app.db.state_machine import TaskState
from app.db.transitions import LeaseOwnershipError, request_operator_cancel
from app.db.worker_ops import WorkerOperationError, start_worker_attempt, submit_worker_result
from app.redaction.repository import redact_for_task
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_worker_control import (
    _HASH,
    _prepared_claim,
    _record_preparation,
    _start,
)


def _valid_result(task_id: uuid.UUID):
    return {
        "schema_version": "result.v1",
        "task_id": str(task_id),
        "attempt": 1,
        "worker": "ollama",
        "status": "succeeded",
        "output": {"text": "accepted output", "files": []},
        "checks": [],
        "usage": {"wall_seconds": 0.1},
        "timing": {
            "started_at": "2026-11-03T00:00:01+00:00",
            "finished_at": "2026-11-03T00:00:02+00:00",
        },
        "error": None,
        "redaction": {"applied": False, "placeholders": 0},
        "payload_hash": _HASH,
        "log_ref": "runs/synthetic.log",
    }


def test_result_cannot_implicitly_start_prepared_lease(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    with pytest.raises(WorkerOperationError, match="explicitly started"):
        submit_worker_result(db_session, lease_id=lease_id, result_payload=_valid_result(task_id))
    db_session.rollback()
    assert db_session.get(Task, task_id).status == TaskState.LEASED
    assert db_session.scalar(select(Result)) is None


@pytest.mark.parametrize(
    "change",
    [
        {"worker": "claude"},
        {"worker": "codex"},
        {"attempt": 2},
        {"payload_hash": "b" * 64},
        {"redaction": {"applied": True, "placeholders": 1}},
    ],
)
def test_result_rejects_identity_and_payload_metadata_mismatch(
    db_session: Session, change: dict
) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    result = _valid_result(task_id) | change
    with pytest.raises(WorkerOperationError):
        submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    db_session.rollback()
    assert db_session.get(Task, task_id).status == TaskState.RUNNING
    assert db_session.scalar(select(Result)) is None


def test_wrong_lease_cannot_submit_even_matching_hash(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    with pytest.raises((WorkerOperationError, LeaseOwnershipError)):
        submit_worker_result(
            db_session, lease_id=uuid.uuid4(), result_payload=_valid_result(task_id)
        )
    db_session.rollback()
    assert db_session.scalar(select(Result)) is None


def test_invalid_result_error_does_not_echo_untrusted_fields(db_session: Session) -> None:
    task_id, lease_id = _prepared_claim(db_session)
    result = _valid_result(task_id)
    result["unexpected-private-field"] = "synthetic-private-value"
    with pytest.raises(WorkerOperationError) as error:
        submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    assert "synthetic-private-value" not in str(error.value)
    assert "unexpected-private-field" not in str(error.value)


def test_acceptance_has_no_task_lock_and_cancellation_wins(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import worker_ops

    task_id, lease_id = _prepared_claim(db_session)
    _start(db_session, task_id, lease_id)
    db_session.commit()
    original_acceptance = worker_ops.evaluate_acceptance

    def cancel_during_acceptance(rules, output):
        with Session(db_session.get_bind()) as other:
            task = other.scalar(select(Task).where(Task.id == task_id).with_for_update(nowait=True))
            assert task is not None
            request_operator_cancel(other, task=task, reason="during_acceptance")
            other.commit()
        return original_acceptance(rules, output)

    monkeypatch.setattr(worker_ops, "evaluate_acceptance", cancel_during_acceptance)
    with pytest.raises(LeaseOwnershipError):
        submit_worker_result(db_session, lease_id=lease_id, result_payload=_valid_result(task_id))
    db_session.rollback()
    assert db_session.get(Task, task_id).cancel_requested
    assert db_session.scalar(select(Result)) is None


def _cloud_attempt(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    payload = copy.deepcopy(_minimal_task_payload(uuid.uuid4()))
    payload["data_class"] = "cloud_redacted"
    payload["fallback_chain"] = ["claude"]
    payload["acceptance"] = [{"type": "must_contain", "value": "synthetic@example.invalid"}]
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    approve_task(session, task)
    session.commit()
    claimed = dequeue_next_queued_task(
        session, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5)
    )
    assert claimed is not None and claimed.lease is not None
    lease_id = claimed.lease.lease_id
    outcome = redact_for_task(
        session, task_id=claimed.id, text="synthetic@example.invalid", attempt=claimed.attempt
    )
    _record_preparation(session, claimed, lease_id, applied=True, placeholders=1)
    session.commit()
    start_worker_attempt(
        session, task_id=claimed.id, lease_id=lease_id, attempt=1, worker=Worker.CLAUDE
    )
    session.commit()
    result = _valid_result(claimed.id)
    result["worker"] = "claude"
    result["redaction"] = {"applied": True, "placeholders": 1}
    result["output"]["text"] = outcome.text
    return claimed.id, lease_id, result


def test_cloud_result_is_rehydrated_locally_before_acceptance(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_id, lease_id, result = _cloud_attempt(db_session, monkeypatch, tmp_path)
    updated = submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    db_session.commit()
    assert updated.status == TaskState.REVIEW
    stored = db_session.scalar(select(Result).where(Result.task_id == task_id))
    assert stored.payload["output"]["text"] == "synthetic@example.invalid"
    assert stored.payload["checks"][0]["passed"] is True
    assert result["output"]["text"].startswith("[[CD_")


def test_unknown_cloud_placeholder_becomes_acceptance_failure_in_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task_id, lease_id, result = _cloud_attempt(db_session, monkeypatch, tmp_path)
    result["output"]["text"] = f"[[CD_{task_id.hex}_EMAIL_999]]"
    updated = submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    db_session.commit()
    assert updated.status == TaskState.REVIEW
    assert updated.attempt == 1 and updated.next_attempt_at is None
    stored = db_session.scalar(select(Result).where(Result.task_id == task_id))
    assert stored.payload["output"]["text"] == ""
    assert stored.payload["error"]["code"] == "acceptance_failed"
    event = db_session.scalar(
        select(Event).where(Event.task_id == task_id, Event.event_type == "task.review")
    )
    assert event.detail["reason"] == "acceptance_failed"
    assert event.detail["retry_action"] == "fail"
