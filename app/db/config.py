"""Database configuration from environment."""

from __future__ import annotations

import os
from functools import lru_cache


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


@lru_cache(maxsize=1)
def get_engine_kwargs() -> dict[str, bool]:
    """Engine options shared by runtime and Alembic."""
    return {"pool_pre_ping": True}
