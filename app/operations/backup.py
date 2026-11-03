"""Atomic database backups with snapshot-consistent manifests and private storage."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.operations.audit import record_operation
from app.operations.errors import OperationError
from app.operations.files import (
    READ_FLAGS,
    WRITE_FLAGS,
    child_directory,
    digest_file,
    private_root,
    read_file,
    remove_tree,
    write_file,
)
from app.operations.process import run_command

MAXIMUM_DUMP_BYTES = 1024 * 1024 * 1024
_BACKUP_NAME = re.compile(r"backup-[0-9a-f]{32}\Z")
_TABLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


class BackupManifest(BaseModel):
    """Private evidence for one completed dump, without database URLs or passwords."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    operation_id: UUID
    created_at: datetime
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAXIMUM_DUMP_BYTES)
    schema_revision: str = Field(pattern=r"^[0-9]{8}_[0-9]{4}$")
    row_counts: dict[str, int]


@dataclass(frozen=True)
class BackupResult:
    operation_id: UUID
    name: str
    sha256: str
    size_bytes: int


def postgres_environment(url: str) -> dict[str, str]:
    """Pass connection fields through a minimal environment, never command arguments."""
    try:
        parsed = make_url(url)
        if (
            parsed.drivername not in {"postgresql", "postgresql+psycopg"}
            or not parsed.database
            or parsed.query
        ):
            raise ValueError
        env = {
            "PATH": os.defpath,
            "PGDATABASE": parsed.database,
            "PGCONNECT_TIMEOUT": "10",
            "PGAPPNAME": "control-deck-maintenance",
        }
        for key, value in (
            ("PGHOST", parsed.host),
            ("PGPORT", parsed.port),
            ("PGUSER", parsed.username),
            ("PGPASSWORD", parsed.password),
        ):
            if value is not None:
                env[key] = str(value)
        return env
    except (ValueError, TypeError, ArgumentError):
        raise OperationError("invalid_database_configuration") from None


def table_counts(connection: Connection) -> dict[str, int]:
    """Count the exact public tables in the caller's snapshot using trusted identifiers."""
    names = sorted(inspect(connection).get_table_names(schema="public"))
    if not 1 <= len(names) <= 128 or any(not _TABLE_NAME.fullmatch(name) for name in names):
        raise OperationError("unsupported_database_schema")
    return {
        name: int(connection.scalar(text(f'SELECT count(*) FROM public."{name}"')) or 0)
        for name in names
    }


def _manifest(data: bytes) -> BackupManifest:
    try:
        result = BackupManifest.model_validate_json(data)
        if (
            result.created_at.tzinfo is None
            or not 1 <= len(result.row_counts) <= 128
            or any(
                not _TABLE_NAME.fullmatch(name) or type(count) is not int or count < 0
                for name, count in result.row_counts.items()
            )
        ):
            raise ValueError
        return result
    except (ValidationError, ValueError):
        raise OperationError("invalid_backup_manifest") from None


def read_manifest(directory: int) -> BackupManifest:
    manifest = _manifest(read_file(directory, "manifest.json", maximum=65536))
    if read_file(directory, "dump.sha256", maximum=65) != (manifest.sha256 + "\n").encode():
        raise OperationError("backup_checksum_mismatch")
    return manifest


def create_backup(
    url: str,
    root: Path,
    *,
    dump_command: tuple[str, ...] = ("pg_dump",),
    timeout_s: float = 300,
) -> BackupResult:
    """Publish a complete backup set; failed staging is never eligible for restore."""
    environment = postgres_environment(url)
    operation_id = uuid4()
    name = "backup-" + operation_id.hex
    pending = ".pending-" + operation_id.hex
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            record_operation(
                session, operation_id=operation_id, operation_type="backup", phase="started"
            )
            session.commit()
        with private_root(root, create=True) as parent:
            with child_directory(parent, pending, create=True) as directory:
                descriptor = os.open("database.dump", WRITE_FLAGS, 0o600, dir_fd=directory)
                try:
                    os.fchmod(descriptor, 0o600)
                    with (
                        engine.connect().execution_options(
                            isolation_level="REPEATABLE READ"
                        ) as connection,
                        connection.begin(),
                    ):
                        connection.execute(text("SET TRANSACTION READ ONLY"))
                        snapshot = str(connection.scalar(text("SELECT pg_export_snapshot()")))
                        counts = table_counts(connection)
                        revision = str(
                            connection.scalar(text("SELECT version_num FROM alembic_version"))
                        )
                        run_command(
                            (
                                *dump_command,
                                "--format=custom",
                                "--schema=public",
                                "--no-owner",
                                "--no-privileges",
                                "--snapshot",
                                snapshot,
                            ),
                            env=environment,
                            output_fd=descriptor,
                            timeout_s=timeout_s,
                            maximum_output=MAXIMUM_DUMP_BYTES,
                        )
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                reader = os.open("database.dump", READ_FLAGS, dir_fd=directory)
                try:
                    sha256, size = digest_file(reader, maximum=MAXIMUM_DUMP_BYTES)
                finally:
                    os.close(reader)
                manifest = BackupManifest(
                    operation_id=operation_id,
                    created_at=datetime.now(UTC),
                    sha256=sha256,
                    size_bytes=size,
                    schema_revision=revision,
                    row_counts=counts,
                )
                write_file(directory, "dump.sha256", (sha256 + "\n").encode())
                write_file(directory, "manifest.json", manifest.model_dump_json().encode())
                os.fsync(directory)
            os.rename(pending, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="backup",
                phase="completed",
                sha256=sha256,
                counts={"bytes": size},
            )
            session.commit()
        return BackupResult(operation_id, name, sha256, size)
    except Exception as error:
        # A published set remains usable if only audit persistence failed.
        try:
            with Session(engine) as session:
                record_operation(
                    session,
                    operation_id=operation_id,
                    operation_type="backup",
                    phase="failed",
                    reason="backup_failed",
                )
                session.commit()
        except Exception:
            pass
        if isinstance(error, OperationError):
            raise
        raise OperationError("backup_failed") from None
    finally:
        engine.dispose()


def prune_backups(root: Path, *, apply: bool = False, now: datetime | None = None) -> int:
    """Prune complete sets older than 14 days only when a newer valid set exists."""
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise OperationError("invalid_clock")
    cutoff = instant - timedelta(days=14)
    with private_root(root) as parent:
        candidates: list[tuple[str, datetime]] = []
        for name in os.listdir(parent):
            if not _BACKUP_NAME.fullmatch(name):
                continue
            with child_directory(parent, name) as directory:
                manifest = read_manifest(directory)
                if name != "backup-" + manifest.operation_id.hex:
                    raise OperationError("invalid_backup_manifest")
                reader = os.open("database.dump", READ_FLAGS, dir_fd=directory)
                try:
                    digest, size = digest_file(reader, maximum=MAXIMUM_DUMP_BYTES)
                finally:
                    os.close(reader)
                if (digest, size) != (manifest.sha256, manifest.size_bytes):
                    raise OperationError("backup_checksum_mismatch")
                candidates.append((name, manifest.created_at))
        if not any(cutoff <= created <= instant for _, created in candidates):
            return 0
        expired = [name for name, created in candidates if created < cutoff]
        if apply:
            for name in expired:
                remove_tree(parent, name)
        return len(expired)


def backup_summary(result: BackupResult) -> str:
    """Return safe command output without configured paths or connection details."""
    return json.dumps(
        {
            "operation_id": str(result.operation_id),
            "sha256": result.sha256,
            "bytes": result.size_bytes,
            "status": "completed",
        }
    )
