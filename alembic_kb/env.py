"""Separate migration environment restricted to an explicitly named test DB."""

from __future__ import annotations

from sqlalchemy import create_engine, pool

from alembic import context
from app.kb.config import get_kb_test_database_url
from app.kb.models import metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == "deck_kb"
    return True


url = get_kb_test_database_url()
if context.is_offline_mode():
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
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
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
