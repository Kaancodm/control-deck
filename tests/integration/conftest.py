"""Fixtures for Postgres integration tests (CI service container)."""

from __future__ import annotations

import argparse
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import require_test_database_url


@pytest.fixture(autouse=True)
def isolated_run_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker API tests publish only into a private per-test artifact root."""
    monkeypatch.setenv("DECK_RUNS_DIR", str(tmp_path / "runs"))


@pytest.fixture(scope="session")
def migrated_database() -> Generator[None, None, None]:
    """Apply Alembic migrations once per test session against the test database."""
    require_test_database_url()
    reset_engine_for_tests()
    get_engine(for_tests=True)

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.cmd_opts = argparse.Namespace(x=["target=test"])
    command.upgrade(alembic_cfg, "head")
    yield
    reset_engine_for_tests()


@pytest.fixture
def db_session(migrated_database: None) -> Generator[Session, None, None]:
    """Session with row cleanup after each test."""
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(text("TRUNCATE tasks, results, events, leases RESTART IDENTITY CASCADE"))
        if session.scalar(text("SELECT to_regclass('settings')")) is not None:
            session.execute(text("TRUNCATE settings"))
        if session.scalar(text("SELECT to_regclass('lane_status')")) is not None:
            session.execute(text("TRUNCATE lane_status"))
        session.commit()
        session.close()
