"""Routing metadata and atomic daily claim budgets against PostgreSQL."""

from __future__ import annotations

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Task
from app.db.queue import dequeue_next_queued_task
from app.db.repository import approve_task, create_task_from_v1
from app.db.routing import routing_snapshot
from app.db.session import get_engine
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.routing.config import RouterConfig
from tests.integration.test_data_gate import privacy_paths as privacy_paths
from tests.integration.test_database import _insert_task, _minimal_task_payload

_TTL = timedelta(seconds=60)


@pytest.fixture(autouse=True)
def independent_budget_capacity(db_session: Session) -> None:
    """These tests exercise lane budgets without the separate global-capacity limit."""
    update_settings(db_session, {"max_parallel": 16})
    db_session.commit()


def _configure(monkeypatch: pytest.MonkeyPatch, **limits: int) -> None:
    config = RouterConfig.model_validate(
        {
            "version": 1,
            "lanes": {
                worker.value: {"daily_attempt_limit": limits.get(worker.value)} for worker in Worker
            },
        }
    )
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: config)
    monkeypatch.setattr("app.db.repository.get_router_config", lambda: config)
    monkeypatch.setattr("app.db.routing.get_router_config", lambda: config)


def _factory():
    return sessionmaker(bind=get_engine(for_tests=True), autoflush=False)


def test_creation_and_claim_persist_route_without_mutating_task(db_session: Session) -> None:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["fallback_chain"] = ["claude", "codex"]
    task_v1 = TaskV1.model_validate(payload)
    original = copy.deepcopy(task_v1.model_dump(mode="json"))
    task = create_task_from_v1(db_session, task_v1)
    assert task.plan["chain"] == ["ollama"]
    assert task.lane == "ollama"
    approve_task(db_session, task)
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.id == task.id
    assert claimed.plan["chain"][claimed.plan["cursor"]] == claimed.lane == "ollama"
    assert claimed.payload == original == task_v1.model_dump(mode="json")
    event = db_session.scalar(
        select(Event).where(Event.task_id == task.id, Event.event_type == "task.acquire")
    )
    assert event.detail["lane"] == "ollama"
    assert event.detail["budget_date"] == routing_snapshot(db_session).budget_date.isoformat()
    assert "integration test task" not in str(event.detail)


def test_no_allowed_workers_is_terminal_routing_block(db_session: Session) -> None:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["policy"] = {"allowed_workers": [], "forbidden_workers": []}
    task = create_task_from_v1(db_session, TaskV1.model_validate(payload))
    assert task.status == TaskState.BLOCKED
    assert task.lane is None and task.plan["reason"] == "no_allowed_lane"
    assert task.approved_at is None and task.attempt == 0


def test_zero_budget_defers_approved_task_without_terminal_block(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ollama=0)
    task = _insert_task(db_session)
    db_session.commit()
    assert task.status == TaskState.QUEUED
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert task.plan["status"] == "deferred" and task.lane is None
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 0
    db_session.commit()
    _configure(monkeypatch, ollama=1)
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.id == task.id


def test_last_budget_slot_counts_inflight_claim_and_defers_next(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ollama=1)
    first = _insert_task(db_session)
    second = _insert_task(db_session)
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None
    db_session.commit()
    assert claimed.status == TaskState.LEASED
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    unclaimed = second if claimed.id == first.id else first
    db_session.refresh(unclaimed)
    assert unclaimed.status == TaskState.QUEUED and unclaimed.attempt == 0
    assert unclaimed.plan["status"] == "deferred"
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 1


def test_claim_rollback_restores_budget_and_attempt(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ollama=1)
    task_id = _insert_task(db_session).id
    db_session.commit()
    with _factory()() as worker:
        claimed = dequeue_next_queued_task(worker, lease_ttl=_TTL, worker=Worker.OLLAMA)
        assert claimed is not None and claimed.attempt == 1
        assert routing_snapshot(worker).usage[Worker.OLLAMA] == 1
        worker.rollback()
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 0
    task = db_session.get(Task, task_id)
    db_session.refresh(task)
    assert task.attempt == 0 and task.status == TaskState.QUEUED
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.attempt == 1


def test_concurrent_last_slot_never_exceeds_limit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ollama=1)
    _insert_task(db_session)
    _insert_task(db_session)
    db_session.commit()
    factory = _factory()

    def poll():
        with factory() as other:
            other.execute(text("SET LOCAL statement_timeout = '2s'"))
            result = dequeue_next_queued_task(other, lease_ttl=_TTL, worker=Worker.OLLAMA)
            other.commit()
            return result.id if result is not None else None

    with factory() as first:
        claimed = dequeue_next_queued_task(first, lease_ttl=_TTL, worker=Worker.OLLAMA)
        assert claimed is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(poll).result(timeout=5) is None
        first.commit()
    assert poll() is None
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 1
    assert (
        db_session.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "task.acquire")
        )
        == 1
    )


def test_previous_day_claim_does_not_consume_current_budget(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ollama=1)
    task = _insert_task(db_session)
    today = routing_snapshot(db_session).budget_date
    db_session.add(
        Event(
            task_id=task.id,
            event_type="task.acquire",
            actor="system",
            attempt=1,
            from_status=TaskState.QUEUED,
            to_status=TaskState.LEASED,
            detail={"lane": "ollama", "budget_date": (today - timedelta(days=1)).isoformat()},
            created_at=routing_snapshot(db_session).now - timedelta(days=1),
        )
    )
    db_session.commit()
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 0
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 1


@pytest.mark.parametrize("legacy_lane", [None, "unknown-lane"])
def test_unknown_historical_lane_conservatively_counts_every_lane(
    db_session: Session, legacy_lane: str | None
) -> None:
    task = _insert_task(db_session)
    now = routing_snapshot(db_session).now
    db_session.add(
        Event(
            task_id=task.id,
            event_type="task.acquire",
            actor="system",
            attempt=1,
            from_status=TaskState.QUEUED,
            to_status=TaskState.LEASED,
            detail=None if legacy_lane is None else {"lane": legacy_lane},
            created_at=now,
        )
    )
    db_session.flush()
    assert routing_snapshot(db_session).usage == {worker: 1 for worker in Worker}


def test_claim_audit_timestamp_uses_database_clock_not_transaction_start(
    db_session: Session,
) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    db_session.scalar(select(func.now()))
    before = db_session.scalar(select(func.clock_timestamp()))
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None
    event = db_session.scalar(
        select(Event).where(Event.task_id == task.id, Event.event_type == "task.acquire")
    )
    assert event.created_at >= before


def test_exhausted_lane_uses_next_authorised_fallback(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, privacy_paths
) -> None:
    _configure(monkeypatch, claude=0, codex=1)
    payload = _minimal_task_payload(uuid.uuid4())
    payload["data_class"] = "cloud_ok"
    payload["fallback_chain"] = ["claude", "codex"]
    task = create_task_from_v1(db_session, TaskV1.model_validate(payload))
    approve_task(db_session, task)
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.CODEX)
    assert claimed is not None and claimed.lane == "codex"
    assert claimed.plan["chain"] == ["codex"]
    assert routing_snapshot(db_session).usage == {
        Worker.OLLAMA: 0,
        Worker.CLAUDE: 0,
        Worker.CODEX: 1,
    }


def test_disabled_lane_preserves_queued_task_until_reenabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    disabled = RouterConfig.model_validate(
        {
            "version": 1,
            "lanes": {worker.value: {"enabled": worker != Worker.OLLAMA} for worker in Worker},
        }
    )
    monkeypatch.setattr("app.db.queue.get_router_config", lambda: disabled)
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert task.lane is None and task.plan["status"] == "deferred"
    assert task.plan["reason"] == "lane_disabled"
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 0
    assert not db_session.scalars(
        select(Event).where(Event.task_id == task.id, Event.event_type == "task.block")
    ).all()
    db_session.commit()

    monkeypatch.setattr("app.db.queue.get_router_config", RouterConfig)
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.id == task.id
    assert claimed.status == TaskState.LEASED and claimed.attempt == 1
    assert claimed.lane == "ollama" and claimed.plan["status"] == "ready"
    assert routing_snapshot(db_session).usage == {
        Worker.OLLAMA: 1,
        Worker.CLAUDE: 0,
        Worker.CODEX: 0,
    }


def test_new_limit_waits_for_previously_unlimited_claim(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert_task(db_session)
    _insert_task(db_session)
    db_session.commit()
    factory = _factory()
    with factory() as unlimited:
        claimed = dequeue_next_queued_task(unlimited, lease_ttl=_TTL, worker=Worker.OLLAMA)
        assert claimed is not None
        _configure(monkeypatch, ollama=1)
        with factory() as limited:
            limited.execute(text("SET LOCAL statement_timeout = '2s'"))
            assert dequeue_next_queued_task(limited, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
            limited.commit()
        unlimited.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    assert routing_snapshot(db_session).usage[Worker.OLLAMA] == 1
