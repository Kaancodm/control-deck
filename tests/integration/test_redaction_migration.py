"""Redaction-map migration preserves prior tasks and refuses data-loss downgrades."""

from __future__ import annotations

import argparse
import hashlib
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
def revision_0007() -> Generator[tuple[Config, Engine], None, None]:
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(cfg, "head")
    engine = get_engine(for_tests=True)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
    command.downgrade(cfg, "20251103_0007")
    try:
        yield cfg, engine
    finally:
        _teardown_migration_db(cfg, engine)


def _legacy_task(engine: Engine) -> uuid.UUID:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    payload["data_class"] = "cloud_redacted"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks (id, status, data_class, payload) "
                "VALUES (:id, 'awaiting_approval', 'cloud_redacted', CAST(:payload AS jsonb))"
            ),
            {"id": task_id, "payload": json.dumps(payload)},
        )
    return task_id


def test_0008_roundtrip_preserves_existing_task(revision_0007: tuple[Config, Engine]) -> None:
    cfg, engine = revision_0007
    task_id = _legacy_task(engine)
    assert "redaction_maps" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "redaction_maps" in inspect(engine).get_table_names()
    command.check(cfg)
    command.downgrade(cfg, "20251103_0007")
    assert "redaction_maps" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    with engine.begin() as connection:
        assert (
            connection.scalar(text("SELECT id FROM tasks WHERE id = :id"), {"id": task_id})
            == task_id
        )


def test_0008_downgrade_refuses_to_lose_maps(revision_0007: tuple[Config, Engine]) -> None:
    cfg, engine = revision_0007
    task_id = _legacy_task(engine)
    command.upgrade(cfg, "head")
    original = "synthetic original"
    placeholder = f"[[CD_{task_id.hex}_TERM_1]]"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO redaction_maps "
                "(id, task_id, placeholder, kind, original, original_sha256, first_attempt) "
                "VALUES (:id, :task_id, :placeholder, 'TERM', :original, :digest, 1)"
            ),
            {
                "id": uuid.uuid4(),
                "task_id": task_id,
                "placeholder": placeholder,
                "original": original,
                "digest": hashlib.sha256(original.encode()).hexdigest(),
            },
        )
    with pytest.raises(RuntimeError, match="local redaction maps would be lost"):
        command.downgrade(cfg, "20251103_0007")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM redaction_maps")) == 1
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == migration_head()
        )
