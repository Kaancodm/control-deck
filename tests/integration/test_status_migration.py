"""Real PostgreSQL migration rollback guards and deadline fencing regressions."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command
from app.db.models import Event, Lease, Result
from app.db.queue import dequeue_next_queued_task
from app.db.session import get_engine, reset_engine_for_tests
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import (
    TransitionActor,
    TransitionFencingError,
    fenced_transition_task,
    transition_task,
)
from tests.integration.helpers import require_test_database_url
from tests.integration.test_database import _insert_task, _minimal_task_payload
from tests.integration.test_issue_fixes import _task_state_enum_labels, _teardown_migration_db

_NEW_STATES = {"review", "done", "expired"}


@pytest.fixture
def revision_0006() -> Generator[tuple[Config, Engine], None, None]:
    """Use only the protected test DB and restore the latest schema after each case."""
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(cfg, "head")
    engine = get_engine(for_tests=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.downgrade(cfg, "20251103_0006")
    try:
        yield cfg, engine
    finally:
        _teardown_migration_db(cfg, engine)


def _legacy_task(engine: Engine, *, state: str = "succeeded") -> uuid.UUID:
    task_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, status, data_class, payload, attempt, requires_approval, approved_at
                ) VALUES (
                    :id, CAST(:state AS task_state), 'local_only', CAST(:payload AS jsonb),
                    1, true, NOW()
                )
                """
            ),
            {"id": task_id, "state": state, "payload": json.dumps(_minimal_task_payload(task_id))},
        )
    return task_id


def test_migration_0007_roundtrip_preserves_historical_success(
    revision_0006: tuple[Config, Engine],
) -> None:
    cfg, engine = revision_0006
    task_id = _legacy_task(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO events (task_id, event_type, from_status, to_status)
                VALUES (:id, 'task.succeed', 'running', 'succeeded')
                """
            ),
            {"id": task_id},
        )
        old_labels = _task_state_enum_labels(conn)
    assert _NEW_STATES.isdisjoint(old_labels)
    assert "deadline_at" not in {column["name"] for column in inspect(engine).get_columns("tasks")}

    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _NEW_STATES.issubset(_task_state_enum_labels(conn))
        row = conn.execute(
            text("SELECT status, deadline_at FROM tasks WHERE id = :id"), {"id": task_id}
        ).one()
        assert row == ("succeeded", None)
        assert (
            conn.execute(
                text("SELECT to_status FROM events WHERE task_id = :id"), {"id": task_id}
            ).scalar_one()
            == "succeeded"
        )

    command.downgrade(cfg, "20251103_0006")
    with engine.begin() as conn:
        assert _task_state_enum_labels(conn) == old_labels
        assert (
            conn.execute(
                text("SELECT status FROM tasks WHERE id = :id"), {"id": task_id}
            ).scalar_one()
            == "succeeded"
        )
        assert (
            conn.execute(
                text("SELECT to_status FROM events WHERE task_id = :id"), {"id": task_id}
            ).scalar_one()
            == "succeeded"
        )
    assert "deadline_at" not in {column["name"] for column in inspect(engine).get_columns("tasks")}

    command.upgrade(cfg, "head")
    assert "deadline_at" in {column["name"] for column in inspect(engine).get_columns("tasks")}
    with engine.begin() as conn:
        assert conn.execute(
            text("SELECT status, deadline_at FROM tasks WHERE id = :id"), {"id": task_id}
        ).one() == ("succeeded", None)


def test_migration_0007_refuses_downgrade_with_new_task_states(
    revision_0006: tuple[Config, Engine],
) -> None:
    cfg, engine = revision_0006
    command.upgrade(cfg, "head")
    task_id = _legacy_task(engine)
    for state in sorted(_NEW_STATES):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE tasks SET status = CAST(:state AS task_state) WHERE id = :id"),
                {"state": state, "id": task_id},
            )
        with pytest.raises(RuntimeError, match="still reference review, done, or expired"):
            command.downgrade(cfg, "20251103_0006")
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20251103_0007"
            )


@pytest.mark.parametrize("from_status,to_status", [("review", "succeeded"), ("running", "done")])
def test_migration_0007_refuses_downgrade_with_audit_only_references(
    revision_0006: tuple[Config, Engine], from_status: str, to_status: str
) -> None:
    cfg, engine = revision_0006
    command.upgrade(cfg, "head")
    task_id = _legacy_task(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO events (task_id, event_type, from_status, to_status)
                VALUES (:id, 'task.migration_fixture', CAST(:from_status AS task_state),
                        CAST(:to_status AS task_state))
                """
            ),
            {"id": task_id, "from_status": from_status, "to_status": to_status},
        )
    with pytest.raises(RuntimeError, match="still reference review, done, or expired"):
        command.downgrade(cfg, "20251103_0006")
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM tasks WHERE id = :id"), {"id": task_id}
            ).scalar_one()
            == "succeeded"
        )


def test_migration_0007_refuses_to_drop_owner_deadlines(
    revision_0006: tuple[Config, Engine],
) -> None:
    cfg, engine = revision_0006
    command.upgrade(cfg, "head")
    task_id = _legacy_task(engine)
    deadline = datetime.now(tz=UTC) + timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tasks SET deadline_at = :deadline WHERE id = :id"),
            {"id": task_id, "deadline": deadline},
        )
    with pytest.raises(RuntimeError, match="owner deadlines would be lost"):
        command.downgrade(cfg, "20251103_0006")
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT deadline_at FROM tasks WHERE id = :id"), {"id": task_id}
            ).scalar_one()
            == deadline
        )


def test_direct_acquire_cannot_bypass_due_deadline_without_a_sweep(db_session: Session) -> None:
    task = _insert_task(db_session)
    task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    db_session.commit()
    events_before = db_session.scalar(select(func.count()).select_from(Event))

    with pytest.raises(TransitionFencingError, match="not eligible for lease"):
        transition_task(db_session, task=task, trigger=TaskTransition.ACQUIRE)

    db_session.refresh(task)
    assert task.status == TaskState.QUEUED
    assert task.attempt == 0
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == events_before


def test_direct_fenced_success_cannot_bypass_due_deadline_without_a_sweep(
    db_session: Session,
) -> None:
    task = _insert_task(db_session)
    task.deadline_at = datetime.now(tz=UTC) + timedelta(hours=1)
    db_session.commit()
    claimed = dequeue_next_queued_task(db_session, lease_ttl=timedelta(minutes=1))
    assert claimed is not None and claimed.lease is not None
    lease_id = claimed.lease.lease_id
    transition_task(
        db_session,
        task=claimed,
        trigger=TaskTransition.START,
        lease_id=lease_id,
        actor=TransitionActor.RUNNER,
    )
    claimed.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    db_session.commit()
    events_before = db_session.scalar(select(func.count()).select_from(Event))

    result = fenced_transition_task(
        db_session,
        task_id=claimed.id,
        from_state=TaskState.RUNNING,
        lease_id=lease_id,
        trigger=TaskTransition.SUCCEED,
    )

    assert result is None
    db_session.refresh(claimed)
    assert claimed.status == TaskState.RUNNING
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 1
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == events_before
