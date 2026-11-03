"""Runner controls are typed, atomic, and preserve cancellation intent."""

from __future__ import annotations

import argparse
from datetime import timedelta

import pytest
from alembic.config import Config
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.schemas import Worker
from app.db.models import Event, Setting
from app.db.queue import dequeue_next_queued_task
from app.db.settings import RunnerSettingsError, read_settings, update_settings
from app.db.state_machine import TaskState
from tests.integration.test_database import _insert_task


@pytest.fixture(autouse=True)
def isolated_settings(db_session: Session):
    db_session.execute(delete(Setting))
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(delete(Setting))
    db_session.commit()


def test_absent_controls_have_safe_defaults_without_inserting_rows(db_session: Session) -> None:
    settings = read_settings(db_session)
    assert settings.max_parallel == 1
    assert not settings.paused and not settings.stop_requested
    assert settings.lane_paused == {worker: False for worker in Worker}
    assert db_session.scalar(select(Setting.key)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"paused": 1},
        {"paused": "false"},
        {"stop_requested": None},
        {"max_parallel": True},
        {"max_parallel": 0},
        {"max_parallel": 17},
        {"max_parallel": 2.0},
        {"lanes.ollama.paused": "true"},
        {"lanes.unknown.paused": True},
        {"unknown-control": False},
        {"paused": True, "max_parallel": 0},
    ],
)
def test_invalid_changes_never_partially_apply(db_session: Session, changes: dict) -> None:
    with pytest.raises(RunnerSettingsError, match="Runner settings are unavailable or invalid"):
        update_settings(db_session, changes)
    assert db_session.scalar(select(Setting.key)) is None


def test_all_controls_roundtrip_and_read_refreshes_existing_identity(db_session: Session) -> None:
    update_settings(db_session, {"paused": True, "max_parallel": 3, "lanes.codex.paused": True})
    db_session.commit()
    retained = db_session.get(Setting, "paused")
    assert retained.value is True
    result = update_settings(db_session, {"paused": False})
    assert result.paused is False
    assert result.max_parallel == 3
    assert result.lane_paused[Worker.CODEX]
    assert not result.lane_paused[Worker.OLLAMA]
    assert retained.updated_at.tzinfo is not None


@pytest.mark.parametrize("key,value", [("unknown-control", True), ("paused", "false")])
def test_corrupt_stored_settings_fail_closed(db_session: Session, key: str, value: object) -> None:
    db_session.add(Setting(key=key, value=value))
    db_session.flush()
    with pytest.raises(RunnerSettingsError):
        read_settings(db_session)
    with pytest.raises(RunnerSettingsError):
        dequeue_next_queued_task(db_session, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA)


def test_pause_leaves_active_attempt_running_and_stop_latches_cancel(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    claimed = dequeue_next_queued_task(
        db_session, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA
    )
    assert claimed is not None
    db_session.commit()
    update_settings(db_session, {"paused": True})
    assert task.status == TaskState.LEASED and not task.cancel_requested
    update_settings(db_session, {"stop_requested": True})
    db_session.refresh(task)
    assert task.status == TaskState.LEASED and task.cancel_requested
    assert task.cancel_reason == "global_stop"
    update_settings(db_session, {"stop_requested": True})
    events = list(
        db_session.scalars(select(Event).where(Event.event_type == "task.cancel_requested"))
    )
    assert len(events) == 1
    update_settings(db_session, {"stop_requested": False, "paused": False})
    db_session.refresh(task)
    assert task.cancel_requested


def test_settings_change_and_stop_requests_rollback_together(db_session: Session) -> None:
    task = _insert_task(db_session)
    db_session.commit()
    assert (
        dequeue_next_queued_task(db_session, lease_ttl=timedelta(seconds=30), worker=Worker.OLLAMA)
        is not None
    )
    db_session.commit()
    update_settings(db_session, {"stop_requested": True})
    db_session.rollback()
    db_session.refresh(task)
    assert not task.cancel_requested
    assert not read_settings(db_session).stop_requested
    assert (
        db_session.scalar(select(Event.id).where(Event.event_type == "task.cancel_requested"))
        is None
    )


def test_settings_migration_roundtrip_preserves_tasks_and_guards_controls(
    db_session: Session,
) -> None:
    task_id = _insert_task(db_session).id
    db_session.commit()
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(config, "20251103_0008")
    assert "settings" not in inspect(db_session.get_bind()).get_table_names()
    command.upgrade(config, "head")
    command.check(config)
    update_settings(db_session, {"paused": True})
    db_session.commit()
    with pytest.raises(RuntimeError, match="runner control settings would be lost"):
        command.downgrade(config, "20251103_0008")
    assert db_session.scalar(text("SELECT count(*) FROM tasks WHERE id=:id"), {"id": task_id}) == 1
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20251103_0011"
