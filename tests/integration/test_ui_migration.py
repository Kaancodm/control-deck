"""UI session migration preserves task data and protects active owner sessions."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import require_test_database_url
from tests.integration.test_issue_fixes import _teardown_migration_db


@pytest.fixture
def revision_0011():
    require_test_database_url()
    reset_engine_for_tests()
    cfg = Config("alembic.ini")
    cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(cfg, "head")
    engine = get_engine(for_tests=True)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
        connection.execute(text("TRUNCATE ui_sessions, ui_login_state"))
    command.downgrade(cfg, "20251103_0011")
    try:
        yield cfg, engine
    finally:
        with engine.begin() as connection:
            if "ui_sessions" in inspect(connection).get_table_names():
                connection.execute(text("TRUNCATE ui_sessions, ui_login_state"))
        _teardown_migration_db(cfg, engine)


def test_0012_adds_only_authentication_tables_and_roundtrips(revision_0011):
    cfg, engine = revision_0011
    before = set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) - before == {"ui_sessions", "ui_login_state"}
    columns = {value["name"] for value in inspect(engine).get_columns("ui_sessions")}
    assert columns == {
        "token_hash",
        "csrf_hash",
        "authenticated",
        "created_at",
        "expires_at",
        "revoked_at",
    }
    assert "password" not in columns and "token" not in columns
    command.check(cfg)
    command.downgrade(cfg, "20251103_0011")
    assert set(inspect(engine).get_table_names()) == before
    command.upgrade(cfg, "head")


def test_0012_downgrade_refuses_to_drop_live_owner_session(revision_0011):
    cfg, engine = revision_0011
    command.upgrade(cfg, "head")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ui_sessions(token_hash,csrf_hash,authenticated,created_at,expires_at) "
                "VALUES (:token,:csrf,true,:now,:expires)"
            ),
            {
                "token": "a" * 64,
                "csrf": "b" * 64,
                "now": datetime.now(UTC),
                "expires": datetime.now(UTC) + timedelta(hours=1),
            },
        )
    with pytest.raises(RuntimeError, match="revoke active owner sessions"):
        command.downgrade(cfg, "20251103_0011")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ui_sessions")) == 1
        connection.execute(text("UPDATE ui_sessions SET revoked_at=clock_timestamp()"))
    command.downgrade(cfg, "20251103_0011")
