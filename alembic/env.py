"""Alembic migration environment."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.db import models as _models  # noqa: F401 — register ORM metadata
from app.db import ui_auth as _ui_auth  # noqa: F401 — register UI authentication metadata
from app.db.base import Base
from app.db.config import get_database_url, get_test_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _alembic_target() -> str | None:
    opts = config.cmd_opts
    raw = getattr(opts, "x", None) if opts is not None else None
    if raw:
        for item in raw:
            if isinstance(item, str) and item.startswith("target="):
                return item.split("=", 1)[1].strip()
    env_target = os.environ.get("DECK_ALEMBIC_TARGET", "").strip()
    return env_target or None


def _database_url() -> str:
    target = _alembic_target()
    runtime_url = os.environ.get("DECK_DATABASE_URL", "").strip()
    test_url = os.environ.get("DECK_TEST_DATABASE_URL", "").strip()

    if target == "test":
        if not test_url:
            raise RuntimeError("DECK_TEST_DATABASE_URL must be set when Alembic target is 'test'")
        return get_test_database_url()

    if runtime_url and test_url:
        raise RuntimeError(
            "Both DECK_DATABASE_URL and DECK_TEST_DATABASE_URL are set; "
            "pass `-x target=test` (or set DECK_ALEMBIC_TARGET=test) for test migrations"
        )

    if runtime_url:
        return get_database_url()

    raise RuntimeError(
        "DECK_DATABASE_URL is not set; use `-x target=test` with DECK_TEST_DATABASE_URL for tests"
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
