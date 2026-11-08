"""Assistant tables migrate up and down on the test database only."""

from __future__ import annotations

import argparse

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import require_test_database_url


def test_assistant_tables_roundtrip_without_touching_tasks() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(config, "head")
    engine = get_engine(for_tests=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assistant_profile_versions (version, notes) "
                    "VALUES (1, 'owner note')"
                )
            )
            assert connection.scalar(text("SELECT count(*) FROM assistant_profile_versions")) == 1
        command.downgrade(config, "20261104_0018")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20261104_0018"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('assistant_profile_versions')")) is None
            )
            assert connection.scalar(text("SELECT to_regclass('assistant_cards')")) is None
            assert connection.scalar(text("SELECT to_regclass('tasks')")) is not None
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20261108_0020"
            )
            assert connection.scalar(text("SELECT count(*) FROM assistant_profile_versions")) == 0
    finally:
        command.upgrade(config, "head")
        reset_engine_for_tests()


def test_downgrade_refuses_to_drop_loopback_turns() -> None:
    require_test_database_url()
    reset_engine_for_tests()
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(config, "head")
    engine = get_engine(for_tests=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assistant_turns "
                    "(id, data_class, owner_text, reply_text, reply_origin) "
                    "VALUES ("
                    "'00000000-0000-4000-8000-000000000020', "
                    "'local_only', 'satz', 'antwort', 'ollama_loopback')"
                )
            )
        with pytest.raises(RuntimeError, match="assistant_turn_origin"):
            command.downgrade(config, "20261108_0019")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20261108_0020"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM assistant_turns "
                        "WHERE reply_origin = 'ollama_loopback'"
                    )
                )
                == 1
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM assistant_turns"))
        command.upgrade(config, "head")
        reset_engine_for_tests()
