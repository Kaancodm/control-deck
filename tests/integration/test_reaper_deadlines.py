"""Reaper deadline boundaries must not discard an otherwise valid batch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import queue
from app.db.models import Task
from app.db.settings import update_settings
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import LeaseOwnershipError, TransitionFencingError
from tests.integration.test_database import _insert_task


def _claim_task(session: Session) -> Task:
    update_settings(session, {"max_parallel": 2})
    _insert_task(session)
    task = queue.dequeue_next_queued_task(session, lease_ttl=timedelta(minutes=5))
    assert task is not None
    assert task.lease is not None
    return task


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("cancel_requested", [False, True])
def test_reaper_expires_deadline_omitted_by_bounded_sweep(
    db_session: Session, cancel_requested: bool, running: bool
) -> None:
    first = _claim_task(db_session)
    second = _claim_task(db_session)
    now = datetime.now(tz=UTC)
    for offset, task in enumerate((first, second)):
        assert task.lease is not None
        if running:
            queue.transition_task(
                db_session,
                task=task,
                trigger=TaskTransition.START,
                lease_id=task.lease.lease_id,
            )
        task.deadline_at = now - timedelta(seconds=2 - offset)
        task.lease.expires_at = now - timedelta(seconds=10)
    second.cancel_requested = cancel_requested
    db_session.commit()

    # The initial sweep handles only first; the lease batch still contains second.
    assert queue.reap_expired_leases(db_session, limit=1) == 1
    db_session.commit()
    db_session.expire_all()

    for task in (first, second):
        assert task.status == TaskState.EXPIRED
        assert task.lease is None
        assert task.cancel_requested is False
        assert [event.event_type for event in task.events].count("task.expire") == 1
        assert not any(event.event_type == "task.lease_expired" for event in task.events)


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_reaper_preserves_batch_when_deadline_crosses_during_fencing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, cancel_requested: bool
) -> None:
    crossing = _claim_task(db_session)
    retrying = _claim_task(db_session)
    now = datetime.now(tz=UTC)
    crossing.deadline_at = now + timedelta(hours=1)
    crossing.cancel_requested = cancel_requested
    for offset, task in enumerate((crossing, retrying)):
        assert task.lease is not None
        task.lease.expires_at = now - timedelta(seconds=10 - offset)
    db_session.commit()
    original_transition = queue.transition_task
    crossing_trigger = TaskTransition.CANCEL if cancel_requested else TaskTransition.LEASE_EXPIRED

    def advance_before_fencing(*args: Any, **kwargs: Any) -> Any:
        task = kwargs["task"]
        if task.id == crossing.id and kwargs["trigger"] == crossing_trigger:
            task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
            db_session.flush()
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(queue, "transition_task", advance_before_fencing)
    assert queue.reap_expired_leases(db_session, limit=2) == 2
    db_session.commit()
    db_session.expire_all()

    assert crossing.status == TaskState.EXPIRED
    assert crossing.lease is None
    assert [event.event_type for event in crossing.events].count("task.expire") == 1
    assert retrying.status == TaskState.QUEUED
    assert retrying.lease is None
    assert retrying.attempt == 1
    assert [event.event_type for event in retrying.events].count("task.lease_expired") == 1


@pytest.mark.parametrize("error_type", [LeaseOwnershipError, TransitionFencingError])
def test_reaper_does_not_hide_fencing_failure_without_expired_deadline(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    task = _claim_task(db_session)
    assert task.lease is not None
    task.deadline_at = datetime.now(tz=UTC) + timedelta(hours=1)
    task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    db_session.commit()

    def reject_transition(*args: Any, **kwargs: Any) -> Any:
        raise error_type("unrelated fencing failure")

    monkeypatch.setattr(queue, "transition_task", reject_transition)
    with pytest.raises(error_type, match="unrelated fencing failure"):
        queue.reap_expired_leases(db_session)
    db_session.rollback()
    assert task.status == TaskState.LEASED
    assert not any(event.event_type == "task.expire" for event in task.events)
