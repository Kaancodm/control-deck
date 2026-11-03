"""Integration tests against PostgreSQL (queue, migrations, SKIP LOCKED)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import DataClass
from app.db.models import Task
from app.db.queue import dequeue_next_queued_task
from app.db.session import get_engine, reset_engine_for_tests
from app.db.state_machine import TaskState
from tests.integration.helpers import require_database_url


def _minimal_task_payload(task_id: uuid.UUID) -> dict[str, object]:
    return {
        "schema_version": "task.v1",
        "task_id": str(task_id),
        "parent_task_id": None,
        "created_at": "2025-11-03T00:00:00+00:00",
        "requested_by": "system",
        "kind": "custom",
        "title": "db-test",
        "instructions": "integration test task",
        "inputs": [],
        "context_refs": [],
        "data_class": "local_only",
        "policy": {"forbidden_workers": []},
        "acceptance": [],
        "fallback_chain": ["ollama"],
        "labels": {},
    }


def test_alembic_upgrade_and_downgrade_roundtrip() -> None:
    """Migrations apply cleanly and downgrade removes the schema."""
    require_database_url()
    reset_engine_for_tests()
    alembic_cfg = Config("alembic.ini")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    reset_engine_for_tests()


def test_dequeue_skip_locked_two_sessions(db_session: Session) -> None:
    """Two workers dequeue distinct queued tasks under concurrent locks."""
    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    db_session.add_all(
        [
            Task(
                id=id_a,
                status=TaskState.QUEUED,
                data_class=DataClass.LOCAL_ONLY,
                payload=_minimal_task_payload(id_a),
            ),
            Task(
                id=id_b,
                status=TaskState.QUEUED,
                data_class=DataClass.LOCAL_ONLY,
                payload=_minimal_task_payload(id_b),
            ),
        ]
    )
    db_session.commit()

    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    worker_a = factory()
    worker_b = factory()
    try:
        first = dequeue_next_queued_task(worker_a, lease_ttl=timedelta(seconds=30))
        second = dequeue_next_queued_task(worker_b, lease_ttl=timedelta(seconds=30))
        assert first is not None
        assert second is not None
        assert first.id != second.id
        assert {first.id, second.id} == {id_a, id_b}
        assert first.status == TaskState.LEASED
        assert second.status == TaskState.LEASED
        worker_a.commit()
        worker_b.commit()
    finally:
        worker_a.close()
        worker_b.close()


def test_data_class_defaults_to_local_only(db_session: Session) -> None:
    task_id = uuid.uuid4()
    task = Task(
        id=task_id,
        status=TaskState.QUEUED,
        payload=_minimal_task_payload(task_id),
    )
    db_session.add(task)
    db_session.flush()
    db_session.refresh(task)
    assert task.data_class == DataClass.LOCAL_ONLY
