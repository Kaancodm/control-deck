"""Shared helpers for Postgres integration tests."""

from __future__ import annotations

import os

import pytest

from app.db.config import get_test_database_url


def require_test_database_url() -> str:
    if not os.environ.get("DECK_TEST_DATABASE_URL", "").strip():
        pytest.skip("DECK_TEST_DATABASE_URL is not set (Postgres integration tests skipped)")
    return get_test_database_url()
