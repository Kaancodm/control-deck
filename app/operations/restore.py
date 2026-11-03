"""Restore verification restricted to explicitly configured empty test databases."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.operations.audit import record_operation
from app.operations.backup import (
    MAXIMUM_DUMP_BYTES,
    postgres_environment,
    read_manifest,
    table_counts,
)
from app.operations.errors import OperationError
from app.operations.files import READ_FLAGS, child_directory, digest_file, private_root
from app.operations.process import run_command


def validate_restore_databases(target_url: str, audit_url: str) -> None:
    """Require distinct named test databases on the explicitly selected test service."""
    try:
        target, audit = make_url(target_url), make_url(audit_url)
        for value in (target, audit):
            postgres_environment(value.render_as_string(hide_password=False))
            if not value.database or not value.database.endswith("_test"):
                raise ValueError
        if target.database == audit.database or (target.host, target.port, target.username) != (
            audit.host,
            audit.port,
            audit.username,
        ):
            raise ValueError
    except (ValueError, TypeError, ArgumentError):
        raise OperationError("unsafe_restore_target") from None


def restore_backup(
    root: Path,
    name: str,
    *,
    target_url: str,
    audit_url: str,
    restore_command: tuple[str, ...] = ("pg_restore",),
    check_command: tuple[str, ...] | None = None,
    timeout_s: float = 300,
) -> UUID:
    """Restore only into an empty test database; never create/drop databases or roles."""
    validate_restore_databases(target_url, audit_url)
    if re.fullmatch(r"backup-[0-9a-f]{32}", name) is None:
        raise OperationError("invalid_backup_name")
    operation_id = uuid4()
    target = create_engine(target_url, pool_pre_ping=True)
    audit = create_engine(audit_url, pool_pre_ping=True)
    try:
        with target.connect() as connection:
            if (
                inspect(connection).get_table_names(schema="public")
                or connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN "
                        "('public', 'information_schema') AND nspname NOT LIKE 'pg_%'"
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
        with private_root(root) as parent, child_directory(parent, name) as directory:
            manifest = read_manifest(directory)
            if name != "backup-" + manifest.operation_id.hex:
                raise OperationError("invalid_backup_manifest")
            reader = os.open("database.dump", READ_FLAGS, dir_fd=directory)
            try:
                digest, size = digest_file(reader, maximum=MAXIMUM_DUMP_BYTES)
                if (digest, size) != (manifest.sha256, manifest.size_bytes):
                    raise OperationError("backup_checksum_mismatch")
                with Session(audit) as session:
                    record_operation(
                        session,
                        operation_id=operation_id,
                        operation_type="restore",
                        phase="started",
                        sha256=digest,
                    )
                    session.commit()
                os.lseek(reader, 0, os.SEEK_SET)
                run_command(
                    (
                        *restore_command,
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                        "--no-privileges",
                        "--dbname",
                        str(make_url(target_url).database),
                    ),
                    env=postgres_environment(target_url),
                    input_fd=reader,
                    timeout_s=timeout_s,
                    maximum_output=65536,
                )
            finally:
                os.close(reader)
        with target.connect() as connection:
            counts = table_counts(connection)
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if counts != manifest.row_counts or revision != manifest.schema_revision:
                raise OperationError("restore_evidence_mismatch")
        environment = {"PATH": os.defpath, "DECK_TEST_DATABASE_URL": target_url}
        command = check_command or (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(Path(__file__).resolve().parents[2] / "alembic.ini"),
            "-x",
            "target=test",
            "check",
        )
        run_command(command, env=environment, timeout_s=timeout_s, maximum_output=65536)
        with Session(audit) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="restore",
                phase="completed",
                sha256=manifest.sha256,
                counts={"rows": sum(manifest.row_counts.values())},
            )
            session.commit()
        return operation_id
    except Exception as error:
        try:
            with Session(audit) as session:
                record_operation(
                    session,
                    operation_id=operation_id,
                    operation_type="restore",
                    phase="failed",
                    reason="restore_failed",
                )
                session.commit()
        except Exception:
            pass
        if isinstance(error, OperationError):
            raise
        raise OperationError("restore_failed") from None
    finally:
        target.dispose()
        audit.dispose()
