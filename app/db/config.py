"""Database configuration from environment."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from sqlalchemy.engine import make_url


class DatabaseConfigurationError(RuntimeError):
    """Raised when required database settings are missing or invalid."""


def get_database_url() -> str:
    """Return the SQLAlchemy database URL from ``DECK_DATABASE_URL``."""
    url = os.environ.get("DECK_DATABASE_URL", "").strip()
    if not url:
        raise DatabaseConfigurationError(
            "DECK_DATABASE_URL is not set; configure it in the environment or .env file"
        )
    return url


def get_test_database_url() -> str:
    """
    Return ``DECK_TEST_DATABASE_URL`` after verifying the database name ends with ``_test``.

    Integration tests must never use ``DECK_DATABASE_URL``.
    """
    url = os.environ.get("DECK_TEST_DATABASE_URL", "").strip()
    if not url:
        raise DatabaseConfigurationError("DECK_TEST_DATABASE_URL is not set")
    database_name = make_url(url).database or ""
    if not database_name.endswith("_test"):
        raise DatabaseConfigurationError(
            f"refusing to run destructive tests against database {database_name!r}; "
            "name must end with '_test'"
        )
    return url


def get_engine_kwargs() -> dict[str, bool]:
    """Engine options shared by runtime and Alembic."""
    return {"pool_pre_ping": True}


def database_name_from_url(url: str) -> str:
    """Extract the database segment from a SQLAlchemy URL."""
    return make_url(url).database or urlparse(url).path.lstrip("/")
