"""Migration 0064: upgrade, check, guarded downgrade, and re-upgrade on the test DB only."""

from __future__ import annotations

import argparse
import json
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from alembic import command
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import require_test_database_url

HEAD = "20261110_0064"
PREVIOUS = "20261108_0063"
_NEW_TABLES = (
    "assistant_channel_accounts",
    "assistant_threads",
    "assistant_thread_links",
    "assistant_messages",
    "memory_facts",
    "memory_approvals",
)


def _config() -> Config:
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    return config


def _version(connection: Connection) -> set[str]:
    return set(connection.scalars(text("SELECT version_num FROM alembic_version")))


def _nullable(connection: Connection, column: str) -> bool:
    value = connection.scalar(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'events' AND column_name = :column"
        ),
        {"column": column},
    )
    return value == "YES"


_NEW_COLUMNS = (
    ("assistant_messages", "privacy_hold"),
    ("memory_facts", "privacy_hold"),
    ("memory_approvals", "corrects_approval_id"),
)
_CORRECTION_FUNCTION = "memory_is_correction(text, uuid, uuid, uuid, text)"


def _has_column(connection: Connection, table: str, column: str) -> bool:
    count = connection.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return count == 1


def _assert_correction_schema(connection: Connection, *, present: bool) -> None:
    for table, column in _NEW_COLUMNS:
        if present:
            assert _has_column(connection, table, column)
        else:
            assert connection.scalar(text(f"SELECT to_regclass('{table}')")) is None
    function = connection.scalar(
        text("SELECT to_regprocedure(:name)"), {"name": _CORRECTION_FUNCTION}
    )
    assert (function is not None) is present
    constraints = set(
        connection.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname IN ('fk_memory_approvals_corrects', "
                "'uq_memory_approvals_corrects', 'ck_assistant_messages_privacy_hold', "
                "'ck_memory_facts_privacy_hold')"
            )
        )
    )
    expected = {
        "fk_memory_approvals_corrects",
        "uq_memory_approvals_corrects",
        "ck_assistant_messages_privacy_hold",
        "ck_memory_facts_privacy_hold",
    }
    assert constraints == (expected if present else set())


def _seed_task_event(engine: Engine) -> uuid.UUID:
    task_id = uuid.uuid4()
    payload = json.dumps(
        {"schema_version": "task.v1", "task_id": str(task_id), "data_class": "local_only"}
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks (id, status, data_class, payload) "
                "VALUES (:id, 'awaiting_approval', 'local_only', CAST(:payload AS jsonb))"
            ),
            {"id": task_id, "payload": payload},
        )
        connection.execute(
            text(
                "INSERT INTO events (task_id, event_type, to_status) "
                "VALUES (:id, 'task.create', 'awaiting_approval')"
            ),
            {"id": task_id},
        )
    return task_id


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
        if connection.scalar(text("SELECT to_regclass('assistant_messages')")) is not None:
            connection.execute(
                text(
                    "TRUNCATE memory_facts, assistant_messages, assistant_thread_links, "
                    "assistant_threads, memory_approvals"
                )
            )


def test_upgrade_check_downgrade_roundtrip_empty_and_seeded() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    config = _config()
    command.upgrade(config, "head")
    command.check(config)
    engine = get_engine(for_tests=True)
    try:
        _cleanup(engine)
        with engine.connect() as connection:
            assert _version(connection) == {HEAD}
            assert _nullable(connection, "task_id") and _nullable(connection, "to_status")
            seeded = connection.execute(
                text(
                    "SELECT channel, account_label, active FROM assistant_channel_accounts "
                    "ORDER BY channel, account_label"
                )
            ).all()
            assert [tuple(row) for row in seeded] == [("deck_ui", "owner", True)]
            _assert_correction_schema(connection, present=True)
        # Empty database: down and up again.
        command.downgrade(config, PREVIOUS)
        with engine.connect() as connection:
            assert _version(connection) == {PREVIOUS}
            for table in _NEW_TABLES:
                assert connection.scalar(text(f"SELECT to_regclass('{table}')")) is None
            assert not _nullable(connection, "task_id")
            assert not _nullable(connection, "to_status")
            _assert_correction_schema(connection, present=False)
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            _assert_correction_schema(connection, present=True)
        # Seeded database: task rows and task events survive both directions.
        task_id = _seed_task_event(engine)
        command.downgrade(config, PREVIOUS)
        with engine.connect() as connection:
            assert _version(connection) == {PREVIOUS}
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM events WHERE task_id = :id"), {"id": task_id}
                )
                == 1
            )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert _version(connection) == {HEAD}
            assert connection.scalar(text("SELECT count(*) FROM tasks")) == 1
    finally:
        command.upgrade(config, "head")
        _cleanup(engine)
        reset_engine_for_tests()


def test_events_check_separates_task_and_conversation_rows() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    command.upgrade(_config(), "head")
    engine = get_engine(for_tests=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO events (event_type, detail) "
                    "VALUES ('assistant.turn_accepted', '{}'::jsonb)"
                )
            )
        for statement in (
            # A taskless row must use a conversation prefix.
            "INSERT INTO events (event_type) VALUES ('task.create')",
            # A taskless row cannot carry a task status.
            "INSERT INTO events (event_type, to_status) VALUES ('memory.x', 'queued')",
        ):
            with pytest.raises(Exception, match="ck_events_task_or_conversation"):
                with engine.begin() as connection:
                    connection.execute(text(statement))
        task_id = _seed_task_event(engine)
        with pytest.raises(Exception, match="ck_events_task_or_conversation"):
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO events (task_id, event_type) VALUES (:id, 'task.x')"),
                    {"id": task_id},
                )
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM events WHERE task_id IS NULL"))
    finally:
        _cleanup(engine)
        reset_engine_for_tests()


def test_downgrade_refuses_with_taskless_events_and_keeps_them() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    config = _config()
    command.upgrade(config, "head")
    engine = get_engine(for_tests=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO events (event_type) VALUES ('assistant.turn_rejected')")
            )
        with pytest.raises(RuntimeError, match="events_taskless_rows"):
            command.downgrade(config, PREVIOUS)
        with engine.connect() as connection:
            assert _version(connection) == {HEAD}
            assert connection.scalar(text("SELECT count(*) FROM events WHERE task_id IS NULL")) == 1
            assert _nullable(connection, "task_id")
    finally:
        _cleanup(engine)
        command.upgrade(config, "head")
        reset_engine_for_tests()


def test_downgrade_refuses_to_drop_conversation_rows() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    config = _config()
    command.upgrade(config, "head")
    engine = get_engine(for_tests=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assistant_threads (id, title, title_origin, data_class, status) "
                    "VALUES (:id, 'Synthetic topic', 'assistant_proposed', 'local_only', "
                    "'proposed')"
                ),
                {"id": uuid.uuid4()},
            )
        with pytest.raises(RuntimeError, match="conversation_rows"):
            command.downgrade(config, PREVIOUS)
        with engine.connect() as connection:
            assert _version(connection) == {HEAD}
    finally:
        _cleanup(engine)
        command.upgrade(config, "head")
        reset_engine_for_tests()
