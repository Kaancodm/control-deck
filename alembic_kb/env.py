"""Separate migration environment with one explicitly selected knowledge database."""

from __future__ import annotations

from alembic.util import CommandError
from sqlalchemy import create_engine, pool
from sqlalchemy.exc import SQLAlchemyError

from alembic import context
from app.kb.config import (
    migration_scope,
    require_migration_command,
    select_kb_migration_target,
)
from app.kb.contracts import KnowledgeError
from app.kb.models import metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == "deck_kb"
    return True


def run_offline(url: str) -> None:
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        version_table="alembic_kb_version",
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online(url: str) -> None:
    try:
        connection = create_engine(url, poolclass=pool.NullPool).connect()
    except SQLAlchemyError:
        raise CommandError("knowledge_database_connection_failed") from None
    with connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            version_table="alembic_kb_version",
            compare_type=True,
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


options = context.config.cmd_opts
command = getattr(options, "cmd", (None,))[0]
try:
    selection = select_kb_migration_target(getattr(options, "x", None))
    require_migration_command(selection, getattr(command, "__name__", None))
except KnowledgeError as error:
    raise CommandError(error.code) from None

with migration_scope(selection):
    if context.is_offline_mode():
        run_offline(selection.url)
    else:
        run_online(selection.url)
