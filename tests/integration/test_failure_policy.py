"""Prepared attempt failures persist bounded retries, pauses and fallback progress."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.schemas import ErrorCode, TaskV1, Worker
from app.db.models import Event, Lease, Result, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.settings import read_settings, try_lock_admission
from app.db.state_machine import TaskState
from app.db.transitions import LeaseOwnershipError, request_operator_cancel
from app.db.worker_ops import (
    WorkerOperationError,
    acknowledge_worker_cancel,
    start_worker_attempt,
    submit_worker_result,
)
from app.routing.config import RouterConfig
from app.workers.lane_status import get_lane_status
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_worker_control import _record_preparation
from tests.integration.test_worker_identity import _valid_result


@pytest.fixture(autouse=True)
def empty_blocklist(tmp_path, monkeypatch):
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))


def _queued(session, *, chain=None, max_attempts=5, local_only=False, acceptance=None):
    payload = _minimal_task_payload(uuid.uuid4(), max_attempts=max_attempts)
    payload["data_class"] = "local_only" if local_only else "cloud_ok"
    payload["fallback_chain"] = chain or ["claude", "codex", "ollama"]
    payload["acceptance"] = acceptance or []
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    approve_task(session, task)
    session.commit()
    return task.id


def _claim(session, worker):
    task = dequeue_next_queued_task(session, worker=worker, lease_ttl=timedelta(minutes=5))
    assert task is not None and task.lease is not None
    lease_id = task.lease.lease_id
    _record_preparation(session, task, lease_id)
    session.commit()
    start_worker_attempt(
        session, task_id=task.id, attempt=task.attempt, lease_id=lease_id, worker=worker
    )
    session.commit()
    result = _valid_result(task.id)
    result.update(worker=worker.value, attempt=task.attempt)
    return task, lease_id, result


def _fail(session, lease_id, result, code, *, status="failed"):
    result = copy.deepcopy(result)
    result.update(status=status, error={"code": code, "message": "Synthetic provider failure"})
    task = submit_worker_result(session, lease_id=lease_id, result_payload=result)
    session.commit()
    return task


@pytest.mark.parametrize("code", ["timeout", "worker_unavailable"])
def test_fallback_never_rewinds_and_counts_every_claim(db_session, code):
    task_id = _queued(db_session)
    original = copy.deepcopy(db_session.get(Task, task_id).payload)
    for attempt, worker in enumerate((Worker.CLAUDE, Worker.CODEX, Worker.OLLAMA), start=1):
        task, lease_id, result = _claim(db_session, worker)
        assert task.id == task_id and task.attempt == attempt
        task = _fail(db_session, lease_id, result, code)
        assert task.status == (TaskState.FAILED if attempt == 3 else TaskState.QUEUED)
        if attempt < 3:
            assert (
                dequeue_next_queued_task(db_session, worker=worker, lease_ttl=timedelta(minutes=5))
                is None
            )
            db_session.commit()
    assert task.payload == original
    assert task.deadline_at is None
    assert len(list(db_session.scalars(select(Result)))) == 3


@pytest.mark.parametrize("allowed", [["claude"], ["claude", "ollama"]])
def test_fallback_respects_claim_routing_rule_and_fails_exhausted_chain(
    db_session, monkeypatch, allowed
):
    config = RouterConfig.model_validate(
        {"rules": [{"id": "restricted", "allowed_workers": allowed}]}
    )
    for module in ("queue", "routing", "repository"):
        monkeypatch.setattr(f"app.db.{module}.get_router_config", lambda: config)
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    task = _fail(db_session, lease_id, result, "timeout")
    if len(allowed) == 1:
        assert task.status == TaskState.FAILED
        assert task.next_attempt_at is None
    else:
        assert task.status == TaskState.QUEUED
        assert (
            dequeue_next_queued_task(
                db_session, worker=Worker.CODEX, lease_ttl=timedelta(minutes=5)
            )
            is None
        )
        db_session.commit()
        task, _, _ = _claim(db_session, Worker.OLLAMA)
        assert task.attempt == 2


def test_rate_limits_backoff_then_fallback_survives_new_sessions(db_session):
    task_id = _queued(db_session)
    for attempt, delay in ((1, 60), (2, 240), (3, 0)):
        task, lease_id, result = _claim(db_session, Worker.CLAUDE)
        before = db_session.scalar(select(func.clock_timestamp()))
        task = _fail(db_session, lease_id, result, "rate_limited")
        assert task.attempt == attempt
        assert task.status == TaskState.QUEUED
        assert task.retry_state["rate_limits"]["claude"] == attempt
        assert task.next_attempt_at >= before + timedelta(seconds=delay)
        if delay:
            with Session(db_session.get_bind()) as restarted:
                assert (
                    dequeue_next_queued_task(
                        restarted, worker=Worker.CLAUDE, lease_ttl=timedelta(minutes=5)
                    )
                    is None
                )
                assert (
                    dequeue_next_queued_task(
                        restarted, worker=Worker.CODEX, lease_ttl=timedelta(minutes=5)
                    )
                    is None
                )
                restarted.commit()
            task.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            db_session.commit()
    task, _, _ = _claim(db_session, Worker.CODEX)
    assert task.id == task_id and task.attempt == 4
    assert task.next_attempt_at is None


def test_core_acceptance_retries_same_worker_until_global_budget(db_session):
    task_id = _queued(
        db_session, max_attempts=3, acceptance=[{"type": "must_contain", "value": "required"}]
    )
    for attempt in range(1, 4):
        task, lease_id, result = _claim(db_session, Worker.CLAUDE)
        # Claimed worker checks do not override the host acceptance result.
        result["checks"] = [{"type": "must_contain", "passed": True, "detail": "claimed"}]
        task = submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
        db_session.commit()
        assert task.attempt == attempt
        assert task.status == (TaskState.FAILED if attempt == 3 else TaskState.QUEUED)
        assert task.retry_state["pinned_worker"] == "claude"
        event = db_session.scalar(
            select(Event).where(Event.task_id == task_id).order_by(Event.id.desc()).limit(1)
        )
        assert event.detail["reason"] == "acceptance_failed"
        assert event.detail["result_status"] == "failed"
    assert db_session.scalar(select(func.count()).select_from(Result)) == 3


def test_internal_retry_is_task_wide_not_reset_by_other_failures(db_session):
    _queued(db_session)
    for worker, code, expected in (
        (Worker.CLAUDE, "internal", TaskState.QUEUED),
        (Worker.CLAUDE, "timeout", TaskState.QUEUED),
        (Worker.CODEX, "internal", TaskState.FAILED),
    ):
        _, lease_id, result = _claim(db_session, worker)
        task = _fail(db_session, lease_id, result, code)
        assert task.status == expected
        assert task.retry_state["internal_retries"] == 1


@pytest.mark.parametrize(
    "code,status",
    [
        ("auth_required", "failed"),
        ("auth_required", "blocked"),
        ("policy_blocked", "failed"),
        ("policy_blocked", "blocked"),
    ],
)
@pytest.mark.parametrize("max_attempts", [1, 5])
def test_provider_refusal_atomically_pauses_lane_even_when_budget_exhausted(
    db_session, code, status, max_attempts
):
    _queued(db_session, max_attempts=max_attempts)
    _, lease_id, result = _claim(db_session, Worker.CLAUDE)
    task = _fail(db_session, lease_id, result, code, status=status)
    assert read_settings(db_session).lane_paused[Worker.CLAUDE]
    observation = get_lane_status(db_session, lane=Worker.CLAUDE)
    assert observation.reason == code
    assert observation.status == ("auth_required" if code == "auth_required" else "paused")
    assert task.status == (TaskState.FAILED if max_attempts == 1 else TaskState.QUEUED)
    if max_attempts > 1:
        task, _, _ = _claim(db_session, Worker.CODEX)
        assert task.attempt == 2


def test_lane_pause_is_serialized_with_admission_before_task_lock(db_session, monkeypatch):
    from app.db import worker_ops

    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    original = worker_ops.lock_admission
    called = []

    def assert_lock_order(session):
        with Session(session.get_bind()) as observer:
            assert (
                observer.scalar(select(Task).where(Task.id == task.id).with_for_update(nowait=True))
                is not None
            )
        original(session)
        with Session(session.get_bind()) as observer:
            assert not try_lock_admission(observer)
        called.append(True)

    monkeypatch.setattr(worker_ops, "lock_admission", assert_lock_order)
    _fail(db_session, lease_id, result, "auth_required")
    assert called == [True]


def test_lost_lease_while_waiting_for_admission_cannot_pause_lane(db_session, monkeypatch):
    from app.db import worker_ops

    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    before = copy.deepcopy(task.retry_state)
    original = worker_ops.lock_admission

    def expire_before_lock(session):
        with Session(session.get_bind()) as observer:
            lease = observer.get(Lease, lease_id)
            lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            observer.commit()
        original(session)

    monkeypatch.setattr(worker_ops, "lock_admission", expire_before_lock)
    with pytest.raises(LeaseOwnershipError):
        _fail(db_session, lease_id, result, "auth_required")
    db_session.rollback()
    assert task.retry_state == before and task.status == TaskState.RUNNING
    assert not read_settings(db_session).lane_paused[Worker.CLAUDE]
    assert get_lane_status(db_session, lane=Worker.CLAUDE) is None


@pytest.mark.parametrize(
    "change", [{"worker": "codex"}, {"attempt": 2}, {"payload_hash": "b" * 64}]
)
def test_invalid_failure_identity_cannot_change_policy_or_pause_lane(db_session, change):
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    before = copy.deepcopy(task.retry_state)
    with pytest.raises(WorkerOperationError):
        _fail(db_session, lease_id, result | change, "auth_required")
    db_session.rollback()
    assert task.retry_state == before and task.status == TaskState.RUNNING
    assert not read_settings(db_session).lane_paused[Worker.CLAUDE]
    assert get_lane_status(db_session, lane=Worker.CLAUDE) is None


@pytest.mark.parametrize("stale", ["lease", "duplicate"])
def test_stale_failure_never_changes_policy_twice(db_session, stale):
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    if stale == "lease":
        lease = db_session.get(Lease, lease_id)
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
    else:
        _fail(db_session, lease_id, result, "rate_limited")
    before = copy.deepcopy(task.retry_state)
    with pytest.raises((WorkerOperationError, LeaseOwnershipError)):
        _fail(db_session, lease_id, result, "auth_required")
    db_session.rollback()
    assert task.retry_state == before
    assert not read_settings(db_session).lane_paused[Worker.CLAUDE]


@pytest.mark.parametrize("code", list(ErrorCode))
def test_local_only_failure_never_admits_cloud(db_session, code):
    _queued(db_session, local_only=True)
    task, lease_id, result = _claim(db_session, Worker.OLLAMA)
    task = _fail(db_session, lease_id, result, code.value)
    for worker in (Worker.CLAUDE, Worker.CODEX):
        assert (
            dequeue_next_queued_task(db_session, worker=worker, lease_ttl=timedelta(minutes=5))
            is None
        )
        db_session.commit()
    assert task.status != TaskState.EXPIRED
    assert task.retry_state["cursor"] <= 1


def test_owner_cancel_wins_over_provider_failure(db_session):
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    request_operator_cancel(db_session, task=task, reason="api")
    db_session.commit()
    with pytest.raises(LeaseOwnershipError):
        _fail(db_session, lease_id, result, "auth_required")
    db_session.rollback()
    acknowledge_worker_cancel(
        db_session, task_id=task.id, attempt=task.attempt, lease_id=lease_id, worker=Worker.CLAUDE
    )
    db_session.commit()
    assert task.status == TaskState.CANCELLED
    assert not read_settings(db_session).lane_paused[Worker.CLAUDE]


def test_deadline_during_backoff_expires_without_another_attempt(db_session):
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    task = _fail(db_session, lease_id, result, "rate_limited")
    task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    reap_expired_leases(db_session)
    db_session.commit()
    assert task.status == TaskState.EXPIRED and task.attempt == 1


def test_privacy_block_between_attempts_never_falls_back(db_session, tmp_path):
    _queued(db_session)
    task, lease_id, result = _claim(db_session, Worker.CLAUDE)
    task = _fail(db_session, lease_id, result, "timeout")
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("integration test task\n", encoding="utf-8")
    assert (
        dequeue_next_queued_task(db_session, worker=Worker.CODEX, lease_ttl=timedelta(minutes=5))
        is None
    )
    db_session.commit()
    assert task.status == TaskState.BLOCKED and task.attempt == 1
    assert not any(read_settings(db_session).lane_paused.values())
    blocklist.write_text("", encoding="utf-8")
    for worker in Worker:
        assert (
            dequeue_next_queued_task(db_session, worker=worker, lease_ttl=timedelta(minutes=5))
            is None
        )
        db_session.commit()
    assert task.status == TaskState.BLOCKED
