"""Retry metadata migration preserves tasks and refuses destructive downgrades."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Generator

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import migration_head, require_test_database_url
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_issue_fixes import _teardown_migration_db


@pytest.fixture
def revision_0010() -> Generator[tuple[Config, Engine], None, None]:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(cfg, "head")
    engine = get_engine(for_tests=True)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.downgrade(cfg, "20251103_0010")
    try:
        yield cfg, engine
    finally:
        _teardown_migration_db(cfg, engine)


def _legacy_task(engine: Engine) -> tuple[uuid.UUID, dict[str, object]]:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks (id, status, data_class, payload) "
                "VALUES (:id, 'awaiting_approval', 'local_only', CAST(:payload AS jsonb))"
            ),
            {"id": task_id, "payload": json.dumps(payload)},
        )
    return task_id, payload


def test_0011_roundtrip_preserves_payload_and_no_data_class_default(
    revision_0010: tuple[Config, Engine],
) -> None:
    cfg, engine = revision_0010
    task_id, payload = _legacy_task(engine)
    command.upgrade(cfg, "head")
    columns = {column["name"]: column for column in inspect(engine).get_columns("tasks")}
    assert columns["retry_state"]["nullable"] and columns["next_attempt_at"]["nullable"]
    assert columns["retry_state"]["default"] is None
    assert columns["data_class"]["default"] is None
    index = next(
        item
        for item in inspect(engine).get_indexes("tasks")
        if item["name"] == "ix_tasks_queued_next_attempt"
    )
    assert index["column_names"] == ["next_attempt_at", "created_at"]
    assert "queued" in str(index["dialect_options"]["postgresql_where"])
    command.check(cfg)
    command.downgrade(cfg, "20251103_0010")
    assert "retry_state" not in {column["name"] for column in inspect(engine).get_columns("tasks")}
    command.upgrade(cfg, "head")
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT payload, retry_state, next_attempt_at FROM tasks WHERE id = :id"),
            {"id": task_id},
        ).one()
        assert row.payload == payload
        assert row.retry_state is None and row.next_attempt_at is None


@pytest.mark.parametrize("field", ["retry_state", "next_attempt_at"])
def test_0011_downgrade_refuses_to_erase_retry_progress(
    revision_0010: tuple[Config, Engine], field: str
) -> None:
    cfg, engine = revision_0010
    task_id, _ = _legacy_task(engine)
    command.upgrade(cfg, "head")
    statement = (
        'UPDATE tasks SET retry_state = \'{"version": 1, "cursor": 0}\'::jsonb WHERE id = :id'
        if field == "retry_state"
        else "UPDATE tasks SET next_attempt_at = clock_timestamp() WHERE id = :id"
    )
    with engine.begin() as connection:
        connection.execute(text(statement), {"id": task_id})
    with pytest.raises(RuntimeError, match="task retry progress or backoff would be lost"):
        command.downgrade(cfg, "20251103_0010")
    with engine.begin() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == migration_head()
        )
