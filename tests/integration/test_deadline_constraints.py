"""Owner deadline validation and the partial sweep-index contract."""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Generator
from datetime import datetime, timedelta

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.api.app import create_app
from app.api.deps import get_db
from app.db.models import Event, Task
from app.db.session import get_engine
from tests.integration.test_database import _minimal_task_payload

_OWNER_HEADERS = {"Authorization": "Bearer test-deadline-owner"}
_INDEX_NAME = "ix_tasks_active_deadline"


@pytest.fixture
def deadline_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "test-deadline-owner")
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("age", [timedelta(minutes=1), timedelta(0)])
def test_elapsed_deadline_rejected_without_task_or_event(
    deadline_client: TestClient, db_session: Session, age: timedelta
) -> None:
    task_id = uuid.uuid4()
    now: datetime = db_session.scalar(select(func.clock_timestamp()))
    payload = _minimal_task_payload(task_id)
    payload["deadline_at"] = (now - age).isoformat()

    response = deadline_client.post("/api/v1/tasks", json=payload, headers=_OWNER_HEADERS)

    assert response.status_code == 422, response.text
    assert "deadline_at" in response.text
    assert db_session.get(Task, task_id) is None
    assert db_session.scalar(select(func.count()).select_from(Event)) == 0


@pytest.mark.parametrize("deadline_kind", ["future", "null", "omitted"])
def test_future_or_absent_deadline_is_accepted(
    deadline_client: TestClient, db_session: Session, deadline_kind: str
) -> None:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    if deadline_kind == "future":
        now: datetime = db_session.scalar(select(func.clock_timestamp()))
        payload["deadline_at"] = (now + timedelta(hours=1)).isoformat()
    elif deadline_kind == "null":
        payload["deadline_at"] = None

    response = deadline_client.post("/api/v1/tasks", json=payload, headers=_OWNER_HEADERS)

    assert response.status_code == 201, response.text
    assert response.json()["task"]["status"] == "awaiting_approval"
    task = db_session.get(Task, task_id)
    assert task is not None
    assert "deadline_at" not in task.payload
    assert (task.deadline_at is not None) == (deadline_kind == "future")


def test_deadline_sweep_has_partial_ordered_index(db_session: Session) -> None:
    indexes = inspect(db_session.connection()).get_indexes("tasks")
    index = next((item for item in indexes if item["name"] == _INDEX_NAME), None)
    assert index is not None
    assert index["column_names"] == ["deadline_at", "id"]
    predicate = index["dialect_options"]["postgresql_where"]
    assert "deadline_at IS NOT NULL" in predicate
    for state in ("succeeded", "done", "expired", "failed", "blocked", "cancelled"):
        assert state in predicate
    assert "clock_timestamp" not in predicate
    assert "now()" not in predicate

    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = (
        db_session.execute(
            text(
                "EXPLAIN SELECT id FROM tasks "
                "WHERE deadline_at <= clock_timestamp() "
                "AND status NOT IN "
                "('succeeded', 'done', 'expired', 'failed', 'blocked', 'cancelled') "
                "ORDER BY deadline_at, id LIMIT 32"
            )
        )
        .scalars()
        .all()
    )
    assert _INDEX_NAME in "\n".join(plan)


def test_deadline_index_survives_migration_roundtrip(migrated_database: None) -> None:
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(cfg, "20251103_0006")
    assert _INDEX_NAME not in {
        item["name"] for item in inspect(get_engine(for_tests=True)).get_indexes("tasks")
    }
    command.upgrade(cfg, "head")
    assert _INDEX_NAME in {
        item["name"] for item in inspect(get_engine(for_tests=True)).get_indexes("tasks")
    }
