"""Persisted retry admission cannot rewind workers or consume backoff attempts."""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.retries import RetryState
from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.routing import persist_task_route, retry_state_for_task, routing_snapshot
from app.db.settings import update_settings
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import TransitionFencingError, transition_task
from app.routing.config import RouterConfig
from tests.integration.test_data_gate import privacy_paths as privacy_paths
from tests.integration.test_database import _minimal_task_payload

_TTL = timedelta(seconds=90)


@pytest.fixture(autouse=True)
def routing_configuration(monkeypatch: pytest.MonkeyPatch, privacy_paths) -> None:
    config = RouterConfig()
    for module in ("queue", "routing", "repository"):
        monkeypatch.setattr(f"app.db.{module}.get_router_config", lambda: config)


def _task(session: Session, *, local_only: bool = False, max_attempts: int = 5) -> Task:
    payload = _minimal_task_payload(uuid.uuid4(), max_attempts=max_attempts)
    payload["data_class"] = "local_only" if local_only else "cloud_ok"
    payload["fallback_chain"] = ["claude", "codex", "ollama"]
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    approve_task(session, task)
    session.commit()
    return task


def _claim(session: Session, worker: Worker = Worker.CLAUDE) -> Task | None:
    return dequeue_next_queued_task(session, lease_ttl=_TTL, worker=worker)


def _requeue(session: Session, task: Task) -> None:
    transition_task(session, task=task, trigger=TaskTransition.RETRY, lease_id=task.lease.lease_id)
    session.commit()


def test_claim_persists_actual_worker_and_keeps_immutable_payload(db_session: Session) -> None:
    task = _task(db_session)
    original = copy.deepcopy(task.payload)
    assert task.retry_state is None
    assert _claim(db_session) is task
    db_session.commit()
    assert retry_state_for_task(task) == RetryState(cursor=0, pinned_worker=Worker.CLAUDE)
    assert task.payload == original


def test_backoff_defers_without_claim_and_allows_next_task(db_session: Session) -> None:
    waiting = _task(db_session)
    available = _task(db_session)
    waiting.next_attempt_at = routing_snapshot(db_session).now + timedelta(minutes=4)
    db_session.commit()
    assert _claim(db_session) is available
    assert waiting.attempt == 0 and waiting.status == TaskState.QUEUED
    assert routing_snapshot(db_session).usage[Worker.CLAUDE] == 1
    assert not db_session.scalars(
        select(Event).where(Event.task_id == waiting.id, Event.event_type == "task.acquire")
    ).all()


def test_direct_atomic_acquire_cannot_bypass_backoff(db_session: Session) -> None:
    task = _task(db_session)
    task.next_attempt_at = routing_snapshot(db_session).now + timedelta(minutes=1)
    db_session.commit()
    with pytest.raises(TransitionFencingError):
        transition_task(db_session, task=task, trigger=TaskTransition.ACQUIRE)
    db_session.refresh(task)
    assert task.attempt == 0 and task.status == TaskState.QUEUED
    assert task.next_attempt_at is not None
    assert routing_snapshot(db_session).usage[Worker.CLAUDE] == 0


def test_due_backoff_clears_only_on_successful_claim(db_session: Session) -> None:
    task = _task(db_session)
    task.next_attempt_at = routing_snapshot(db_session).now - timedelta(seconds=1)
    db_session.commit()
    assert _claim(db_session, Worker.CODEX) is None
    assert task.next_attempt_at is not None
    assert _claim(db_session) is task
    assert task.next_attempt_at is None and task.attempt == 1


def test_retry_pin_survives_new_session_and_routing_polls(db_session: Session) -> None:
    task = _task(db_session)
    assert _claim(db_session) is task
    _requeue(db_session, task)
    original_state = copy.deepcopy(task.retry_state)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    with factory() as restarted:
        for worker in (Worker.CODEX, Worker.OLLAMA):
            assert _claim(restarted, worker) is None
            restarted.commit()
        claimed = _claim(restarted)
        assert claimed is not None and claimed.id == task.id
        assert claimed.attempt == 2 and claimed.retry_state == original_state
        restarted.commit()


@pytest.mark.parametrize("unavailable", ["disabled", "paused", "budget"])
def test_pinned_retry_waits_for_same_worker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, unavailable: str
) -> None:
    task = _task(db_session)
    assert _claim(db_session) is task
    _requeue(db_session, task)
    if unavailable == "paused":
        update_settings(db_session, {"lanes.claude.paused": True})
    else:
        config = RouterConfig.model_validate(
            {
                "lanes": {
                    "claude": {
                        "enabled": unavailable != "disabled",
                        "daily_attempt_limit": 1 if unavailable == "budget" else None,
                    }
                }
            }
        )
        monkeypatch.setattr("app.db.queue.get_router_config", lambda: config)
    db_session.commit()
    assert _claim(db_session, Worker.CODEX) is None
    assert _claim(db_session) is None
    db_session.refresh(task)
    assert task.attempt == 1 and task.status == TaskState.QUEUED
    assert task.plan["status"] == "deferred"
    assert task.retry_state["pinned_worker"] == "claude"
    db_session.commit()
    update_settings(db_session, {"lanes.claude.paused": False})
    db_session.commit()
    monkeypatch.setattr("app.db.queue.get_router_config", RouterConfig)
    assert _claim(db_session) is task
    assert task.attempt == 2


def test_initial_paused_lane_does_not_starve_other_authorized_lane(db_session: Session) -> None:
    task = _task(db_session)
    update_settings(db_session, {"lanes.claude.paused": True})
    db_session.commit()
    assert _claim(db_session, Worker.CODEX) is task
    assert task.retry_state["cursor"] == 1
    assert task.retry_state["pinned_worker"] == "codex"
    assert routing_snapshot(db_session).usage[Worker.CLAUDE] == 0
    assert routing_snapshot(db_session).usage[Worker.CODEX] == 1


def test_skipped_first_lane_cannot_reappear_on_same_worker_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(db_session)
    config = RouterConfig.model_validate({"lanes": {"claude": {"daily_attempt_limit": 0}}})
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: config)
    assert _claim(db_session, Worker.CODEX) is task
    _requeue(db_session, task)
    monkeypatch.setattr("app.db.queue.get_router_config", RouterConfig)
    assert _claim(db_session) is None
    assert _claim(db_session, Worker.CODEX) is task
    assert task.retry_state["cursor"] == 1


def test_fallback_cursor_survives_polls_and_excludes_earlier_workers(db_session: Session) -> None:
    task = _task(db_session)
    assert _claim(db_session) is task
    _requeue(db_session, task)
    task.retry_state = RetryState(cursor=1, rate_limits={Worker.CLAUDE: 3}).model_dump(mode="json")
    task.attempt = 3
    db_session.commit()
    for _ in range(3):
        assert _claim(db_session) is None
        db_session.commit()
    assert _claim(db_session, Worker.CODEX) is task
    assert task.retry_state["cursor"] == 1
    assert task.retry_state["rate_limits"] == {"claude": 3}
    assert task.attempt == 4


def test_new_rule_cannot_switch_pinned_worker_or_terminally_block_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(db_session)
    assert _claim(db_session) is task
    _requeue(db_session, task)
    config = RouterConfig.model_validate(
        {"rules": [{"id": "narrower", "allowed_workers": ["codex"]}]}
    )
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: config)
    assert _claim(db_session, Worker.CODEX) is None
    assert task.status == TaskState.QUEUED and task.attempt == 1
    assert task.plan["status"] == "deferred"
    assert task.retry_state["pinned_worker"] == "claude"


def test_owner_deadline_expires_during_backoff(db_session: Session) -> None:
    task = _task(db_session)
    now = routing_snapshot(db_session).now
    task.deadline_at = now - timedelta(seconds=1)
    task.next_attempt_at = now + timedelta(minutes=4)
    db_session.commit()
    assert _claim(db_session) is None
    assert task.status == TaskState.EXPIRED and task.attempt == 0


def test_lease_expiry_preserves_worker_and_remains_bounded(db_session: Session) -> None:
    task = _task(db_session, max_attempts=2)
    assert _claim(db_session) is task
    original_state = copy.deepcopy(task.retry_state)
    task.lease.expires_at = routing_snapshot(db_session).now - timedelta(seconds=1)
    db_session.commit()
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    assert task.status == TaskState.QUEUED and task.retry_state == original_state
    assert _claim(db_session, Worker.CODEX) is None
    assert _claim(db_session) is task
    task.lease.expires_at = routing_snapshot(db_session).now - timedelta(seconds=1)
    db_session.commit()
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    assert task.status == TaskState.FAILED and task.attempt == 2


def test_legacy_claim_seeds_actual_lane_before_temporarily_unavailable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(db_session)
    config = RouterConfig.model_validate({"lanes": {"claude": {"enabled": False}}})
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: config)
    assert _claim(db_session, Worker.CODEX) is task
    _requeue(db_session, task)
    task.retry_state = None
    db_session.commit()
    disabled = RouterConfig.model_validate({"lanes": {"codex": {"enabled": False}}})
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: disabled)
    assert _claim(db_session) is None
    assert task.lane is None and task.retry_state["pinned_worker"] == "codex"
    db_session.commit()
    monkeypatch.setattr("app.db.queue.get_router_config", RouterConfig)
    assert _claim(db_session) is None
    assert _claim(db_session, Worker.CODEX) is task


@pytest.mark.parametrize(
    "state",
    [
        {"cursor": 4},
        {"cursor": 1, "pinned_worker": "claude"},
        {"cursor": 0, "pinned_worker": "unknown"},
        {"version": 2},
        {"internal_retries": -1},
        {"internal_retries": 2},
        {"rate_limits": {"claude": 4}},
        {"rate_limits": {"claude": 1}},
    ],
)
def test_invalid_retry_metadata_is_deferred_without_execution(
    db_session: Session, state: dict[str, object]
) -> None:
    task = _task(db_session)
    task.retry_state = state
    db_session.commit()
    assert _claim(db_session) is None
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert task.plan["reason"] == "retry_metadata_invalid"
    assert (
        db_session.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "task.acquire")
        )
        == 0
    )


def test_local_only_never_accepts_cloud_retry_pin(db_session: Session) -> None:
    task = _task(db_session, local_only=True)
    task.retry_state = RetryState(pinned_worker=Worker.CLAUDE).model_dump(mode="json")
    db_session.commit()
    assert _claim(db_session) is None
    assert _claim(db_session, Worker.OLLAMA) is None
    assert task.status == TaskState.QUEUED and task.attempt == 0


def test_progress_refresh_never_mutates_frozen_payload(db_session: Session) -> None:
    task = _task(db_session)
    original = copy.deepcopy(task.payload)
    task.retry_state = RetryState(cursor=2).model_dump(mode="json")
    task.attempt = 2
    db_session.commit()
    plan = persist_task_route(db_session, task, paused_lanes=frozenset({Worker.OLLAMA}))
    assert plan.status == "deferred"
    assert task.payload == original and task.retry_state["cursor"] == 2
