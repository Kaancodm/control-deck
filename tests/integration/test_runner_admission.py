"""Lane identity, capacity and stop admission share one transactional fence."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event as ThreadEvent

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Setting, Task
from app.db.queue import dequeue_next_queued_task
from app.db.repository import approve_task, create_task_from_v1
from app.db.session import get_engine
from app.db.settings import read_settings, update_settings
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import TransitionActor, transition_task
from tests.integration.test_database import _insert_task, _minimal_task_payload

_TTL = timedelta(seconds=30)


@pytest.fixture(autouse=True)
def all_lanes_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission mechanics must not depend on the shipped lane/account state."""
    router = tmp_path / "router.yaml"
    router.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setenv("DECK_ROUTER_FILE", str(router))


@pytest.fixture
def privacy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocklist = tmp_path / "private-literals.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))


@pytest.fixture(autouse=True)
def isolated_settings(db_session: Session):
    db_session.execute(delete(Setting))
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(delete(Setting))
    db_session.commit()


def _factory():
    return sessionmaker(bind=get_engine(for_tests=True), autoflush=False)


def _cloud_task(session: Session, chain: list[str]) -> Task:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["data_class"] = "cloud_ok"
    payload["fallback_chain"] = chain
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    approve_task(session, task)
    return task


@pytest.mark.parametrize("worker", [Worker.CLAUDE, Worker.CODEX])
def test_cloud_lane_never_claims_local_only_task(db_session: Session, worker: Worker) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=worker) is None
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert db_session.scalar(select(Event.id).where(Event.event_type == "task.acquire")) is None


def test_polling_lane_cannot_reorder_authorized_fallback(
    db_session: Session, privacy_paths
) -> None:
    task = _cloud_task(db_session, ["claude", "codex"])
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.CODEX) is None
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.CLAUDE)
    assert claimed is not None and claimed.id == task.id
    assert claimed.lane == "claude" and claimed.plan["chain"] == ["claude", "codex"]


def test_lane_filter_skips_earlier_different_lane(db_session: Session, privacy_paths) -> None:
    _cloud_task(db_session, ["claude"])
    local = _insert_task(db_session)
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.id == local.id


@pytest.mark.parametrize("control", ["paused", "stop_requested", "lanes.ollama.paused"])
def test_pause_or_stop_prevents_new_claims(db_session: Session, control: str) -> None:
    task = _insert_task(db_session)
    update_settings(db_session, {control: True})
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0


def test_pause_of_another_lane_does_not_prevent_local_claim(db_session: Session) -> None:
    _insert_task(db_session)
    update_settings(db_session, {"lanes.claude.paused": True})
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None


def test_default_capacity_counts_leased_attempts_until_released(db_session: Session) -> None:
    _insert_task(db_session)
    second = _insert_task(db_session)
    db_session.commit()
    first = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert first is not None
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    db_session.commit()
    transition_task(
        db_session,
        task=first,
        trigger=TaskTransition.CANCEL,
        lease_id=first.lease.lease_id,
        actor=TransitionActor.RUNNER,
    )
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.id == second.id


def test_explicit_capacity_allows_multiple_committed_claims(db_session: Session) -> None:
    _insert_task(db_session)
    _insert_task(db_session)
    _insert_task(db_session)
    update_settings(db_session, {"max_parallel": 2})
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(Task).where(Task.status == TaskState.LEASED)
        )
        == 2
    )


def test_simultaneous_claims_cannot_exceed_capacity(db_session: Session) -> None:
    _insert_task(db_session)
    _insert_task(db_session)
    db_session.commit()
    factory = _factory()

    def poll():
        with factory() as session:
            session.execute(text("SET LOCAL statement_timeout='2s'"))
            task = dequeue_next_queued_task(session, lease_ttl=_TTL, worker=Worker.OLLAMA)
            session.commit()
            return task.id if task else None

    with factory() as first:
        claimed = dequeue_next_queued_task(first, lease_ttl=_TTL, worker=Worker.OLLAMA)
        assert claimed is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(poll).result(timeout=5) is None
        first.commit()
    assert poll() is None


def test_uncommitted_stop_blocks_poll_and_rollback_restores_admission(db_session: Session) -> None:
    _insert_task(db_session)
    db_session.commit()
    factory = _factory()
    with factory() as owner:
        update_settings(owner, {"stop_requested": True})
        with factory() as runner:
            assert dequeue_next_queued_task(runner, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
            runner.commit()
        owner.rollback()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is not None


def test_stop_waits_for_claim_then_marks_it_before_returning(db_session: Session) -> None:
    _insert_task(db_session)
    db_session.commit()
    factory = _factory()
    started = ThreadEvent()
    finished = ThreadEvent()

    def stop():
        with factory() as owner:
            owner.execute(text("SET LOCAL statement_timeout='3s'"))
            started.set()
            settings = update_settings(owner, {"stop_requested": True})
            owner.commit()
            finished.set()
            return settings.stop_requested

    with factory() as runner, ThreadPoolExecutor(max_workers=1) as executor:
        claimed = dequeue_next_queued_task(runner, lease_ttl=_TTL, worker=Worker.OLLAMA)
        assert claimed is not None
        task_id = claimed.id
        future = executor.submit(stop)
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.05)
        runner.commit()
        assert future.result(timeout=5)
    task = db_session.get(Task, task_id)
    assert task.cancel_requested
    assert read_settings(db_session).stop_requested
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None


def test_queued_task_at_attempt_limit_cannot_get_another_claim(db_session: Session) -> None:
    task = _insert_task(db_session, max_attempts=1)
    task.attempt = 1
    db_session.commit()
    assert dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA) is None
    db_session.refresh(task)
    assert task.attempt == 1 and task.status == TaskState.QUEUED


def test_claim_lease_uses_current_database_time(db_session: Session) -> None:
    _insert_task(db_session)
    db_session.commit()
    before = db_session.scalar(select(func.clock_timestamp()))
    claimed = dequeue_next_queued_task(db_session, lease_ttl=_TTL, worker=Worker.OLLAMA)
    assert claimed is not None and claimed.lease is not None
    assert claimed.lease.heartbeat_at >= before
    assert claimed.lease.expires_at - claimed.lease.heartbeat_at == _TTL
