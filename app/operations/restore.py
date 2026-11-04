"""Restore verification restricted to explicitly configured empty test databases."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.kb.repository import KnowledgeStore
from app.operations.audit import record_operation
from app.operations.backup import (
    MAXIMUM_DUMP_BYTES,
    SCOPES,
    postgres_environment,
    read_manifest,
    table_counts,
)
from app.operations.environment import operation_path
from app.operations.errors import OperationError
from app.operations.files import READ_FLAGS, child_directory, digest_file, private_root
from app.operations.process import run_command


def validate_restore_databases(
    target_url: str,
    audit_url: str,
    *,
    knowledge_target_url: str | None = None,
    knowledge_source_url: str | None = None,
) -> None:
    """Require four distinct named test databases on the selected test service."""
    if not knowledge_target_url or not knowledge_source_url:
        raise OperationError("knowledge_restore_databases_required")
    try:
        values = [
            make_url(value)
            for value in (target_url, audit_url, knowledge_target_url, knowledge_source_url)
        ]
        endpoints = set()
        names = set()
        for value in values:
            postgres_environment(value.render_as_string(hide_password=False))
            if not value.database or not value.database.endswith("_test"):
                raise ValueError
            endpoints.add((value.host, value.port or 5432, value.username))
            names.add(value.database)
        if len(endpoints) != 1 or len(names) != 4:
            raise ValueError
    except (ValueError, TypeError, ArgumentError):
        raise OperationError("unsafe_restore_target") from None


def _require_empty_target(connection: Connection) -> None:
    """Reject every user object before preparing an isolated restore target."""
    if (
        inspect(connection).get_table_names(schema="public")
        or connection.scalar(
            text(
                "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN "
                "('public', 'information_schema') AND left(nspname, 3) <> 'pg_'"
            )
        )
        or connection.scalar(
            text(
                "SELECT count(*) FROM ("
                "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid "
                "WHERE n.nspname='public' UNION ALL "
                "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid "
                "WHERE n.nspname='public' UNION ALL "
                "SELECT t.oid FROM pg_type t JOIN pg_namespace n ON t.typnamespace=n.oid "
                "WHERE n.nspname='public') AS objects"
            )
        )
    ):
        raise OperationError("restore_target_not_empty")


def restore_backup(
    root: Path,
    name: str,
    *,
    target_url: str,
    audit_url: str,
    knowledge_target_url: str | None = None,
    knowledge_source_url: str | None = None,
    restore_command: tuple[str, ...] = ("pg_restore",),
    check_command: tuple[str, ...] | None = None,
    knowledge_check_command: tuple[str, ...] | None = None,
    timeout_s: float = 300,
) -> UUID:
    """Validate both archives and empty targets first; never report partial success."""
    validate_restore_databases(
        target_url,
        audit_url,
        knowledge_target_url=knowledge_target_url,
        knowledge_source_url=knowledge_source_url,
    )
    assert knowledge_target_url is not None
    if re.fullmatch(r"backup-[0-9a-f]{32}", name) is None:
        raise OperationError("invalid_backup_name")
    operation_id = uuid4()
    urls = {"central": target_url, "knowledge": knowledge_target_url}
    targets = {key: create_engine(value, pool_pre_ping=True) for key, value in urls.items()}
    audit = create_engine(audit_url, pool_pre_ping=True)
    repository = Path(__file__).resolve().parents[2]
    changed = False
    restored = 0
    try:
        for target in targets.values():
            with target.connect() as connection:
                _require_empty_target(connection)
        with (
            private_root(root) as parent,
            child_directory(parent, name) as directory,
            ExitStack() as stack,
        ):
            manifest = read_manifest(directory)
            if name != "backup-" + manifest.operation_id.hex:
                raise OperationError("invalid_backup_manifest")
            readers = {}
            # Validate the complete set before changing either database. Keep the same
            # descriptors pinned for restore, rather than reopening untrusted paths.
            for key, scope in SCOPES.items():
                evidence = getattr(manifest, key)
                config = Config(str(repository / scope.alembic_config))
                config.set_main_option(
                    "script_location",
                    str(repository / ("alembic_kb" if key == "knowledge" else "alembic")),
                )
                if (
                    evidence.schema_revision
                    != ScriptDirectory.from_config(config).get_current_head()
                ):
                    raise OperationError("restore_revision_mismatch")
                reader = os.open(scope.filename, READ_FLAGS, dir_fd=directory)
                stack.callback(os.close, reader)
                readers[key] = reader
                if digest_file(reader, maximum=MAXIMUM_DUMP_BYTES) != (
                    evidence.sha256,
                    evidence.size_bytes,
                ):
                    raise OperationError("backup_checksum_mismatch")
            for target in targets.values():
                with target.connect() as connection:
                    _require_empty_target(connection)
            for key, reader in readers.items():
                os.lseek(reader, 0, os.SEEK_SET)
                run_command(
                    (*restore_command, "--list"),
                    env=postgres_environment(urls[key]),
                    input_fd=reader,
                    timeout_s=timeout_s,
                    maximum_output=65536,
                )
            for target in targets.values():
                with target.connect() as connection:
                    _require_empty_target(connection)
            digest = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
            with Session(audit) as session:
                record_operation(
                    session,
                    operation_id=operation_id,
                    operation_type="restore",
                    phase="started",
                    sha256=digest,
                )
                session.commit()
            for key, target in targets.items():
                with target.begin() as connection:
                    _require_empty_target(connection)
                    connection.execute(text("DROP SCHEMA IF EXISTS public RESTRICT"))
                changed = True
                reader = readers[key]
                os.lseek(reader, 0, os.SEEK_SET)
                run_command(
                    (
                        *restore_command,
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                        "--no-privileges",
                        "--dbname",
                        str(make_url(urls[key]).database),
                    ),
                    env=postgres_environment(urls[key]),
                    input_fd=reader,
                    timeout_s=timeout_s,
                    maximum_output=65536,
                )
                restored += 1
        for key, target in targets.items():
            scope, evidence = SCOPES[key], getattr(manifest, key)
            if key == "knowledge":
                KnowledgeStore(target).check_contract()
            with target.connect() as connection:
                counts = table_counts(connection, key)
                revision = connection.scalar(
                    text(f'SELECT version_num FROM public."{scope.revision_table}"')
                )
                vector_version = (
                    connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                    )
                    if key == "knowledge"
                    else None
                )
                if (
                    counts != evidence.row_counts
                    or revision != evidence.schema_revision
                    or vector_version != evidence.vector_version
                ):
                    raise OperationError("restore_evidence_mismatch")
            environment = {
                "PATH": operation_path(),
                "DECK_KB_TEST_DATABASE_URL"
                if key == "knowledge"
                else "DECK_TEST_DATABASE_URL": urls[key],
            }
            selected = knowledge_check_command if key == "knowledge" else check_command
            command = selected or (
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(repository / scope.alembic_config),
                *(("-x", "target=test") if key == "central" else ()),
                "check",
            )
            run_command(
                command, env=environment, timeout_s=timeout_s, maximum_output=65536, cwd=repository
            )
        with Session(audit) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="restore",
                phase="completed",
                sha256=digest,
                counts={
                    "rows": sum(manifest.central.row_counts.values())
                    + sum(manifest.knowledge.row_counts.values())
                },
            )
            session.commit()
        return operation_id
    except Exception as error:
        reason = "restore_partial_failure" if changed else "restore_failed"
        try:
            with Session(audit) as session:
                record_operation(
                    session,
                    operation_id=operation_id,
                    operation_type="restore",
                    phase="failed",
                    reason=reason,
                    counts={"backups": restored},
                )
                session.commit()
        except Exception:
            pass
        if changed:
            raise OperationError(reason) from None
        if isinstance(error, OperationError):
            raise
        raise OperationError(reason) from None
    finally:
        for target in targets.values():
            target.dispose()
        audit.dispose()
