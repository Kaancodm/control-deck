"""Fixtures for Postgres integration tests (CI service container)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.db.base import Base
from app.db.session import get_engine, reset_engine_for_tests
from tests.integration.helpers import require_database_url


@pytest.fixture(scope="session")
def migrated_database() -> Generator[None, None, None]:
    """Apply Alembic migrations once per test session."""
    require_database_url()
    reset_engine_for_tests()
    get_engine()

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    yield
    reset_engine_for_tests()


@pytest.fixture
def db_session(migrated_database: None) -> Generator[Session, None, None]:
    """Session with row cleanup after each test."""
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(delete(table))
        session.commit()
        session.close()
