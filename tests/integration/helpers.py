"""Shared helpers for Postgres integration tests."""

from __future__ import annotations

import os

import pytest


def require_database_url() -> str:
    url = os.environ.get("DECK_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DECK_DATABASE_URL is not set (Postgres integration tests skipped)")
    return url
