"""Shared helpers for Postgres integration tests."""

from __future__ import annotations

import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.config import get_test_database_url


def migration_head() -> str:
    """Expected repository head for transactional downgrade rollback assertions."""
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert head is not None
    return head


def require_test_database_url() -> str:
    if not os.environ.get("DECK_TEST_DATABASE_URL", "").strip():
        pytest.skip("DECK_TEST_DATABASE_URL is not set (Postgres integration tests skipped)")
    return get_test_database_url()
