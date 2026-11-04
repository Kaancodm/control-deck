"""Atomic complete backup sets with separate snapshot-consistent database dumps."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.operations.audit import record_operation
from app.operations.environment import operation_path
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


@dataclass(frozen=True)
class DatabaseScope:
    filename: str
    checksum: str
    schemas: tuple[str, ...]
    revision_table: str
    alembic_config: str


SCOPES = {
    "central": DatabaseScope(
        "database.dump", "dump.sha256", ("public",), "alembic_version", "alembic.ini"
    ),
    "knowledge": DatabaseScope(
        "knowledge.dump",
        "knowledge.sha256",
        ("public", "deck_kb"),
        "alembic_kb_version",
        "alembic_kb.ini",
    ),
}


class DatabaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAXIMUM_DUMP_BYTES)
    schema_revision: str = Field(pattern=r"^(?:[0-9]{8}_[0-9]{4}|kb_[0-9]{4})$")
    row_counts: dict[str, int]
    vector_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class BackupManifest(BaseModel):
    """Version two requires both databases; legacy central-only sets are incomplete."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[2] = 2
    operation_id: UUID
    created_at: datetime
    central: DatabaseEvidence
    knowledge: DatabaseEvidence


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
            "PATH": operation_path(),
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


def require_knowledge_database(central_url: str, knowledge_url: str | None) -> str:
    """Never silently degrade to a central-only backup or reuse its database."""
    if not knowledge_url:
        raise OperationError("knowledge_database_required")
    postgres_environment(central_url)
    postgres_environment(knowledge_url)
    central, knowledge = make_url(central_url), make_url(knowledge_url)
    if (central.host, central.port or 5432, central.database) == (
        knowledge.host,
        knowledge.port or 5432,
        knowledge.database,
    ):
        raise OperationError("knowledge_database_must_be_separate")
    return knowledge_url


def configured_knowledge_url(*, for_tests: bool) -> str:
    """Select maintenance configuration without enabling any knowledge adapter."""
    if for_tests:
        from app.kb.config import get_kb_test_database_url

        return get_kb_test_database_url()
    value = os.environ.get("DECK_KB_DATABASE_URL", "").strip()
    if not value:
        raise OperationError("knowledge_database_required")
    postgres_environment(value)
    return value


def table_counts(connection: Connection, scope: str = "central") -> dict[str, int]:
    """Count qualified tables in one exported snapshot, using fixed schema scopes."""
    schemas = SCOPES[scope].schemas
    actual = set(
        connection.scalars(
            text(
                "SELECT nspname FROM pg_namespace WHERE nspname <> 'information_schema' "
                "AND left(nspname, 3) <> 'pg_'"
            )
        )
    )
    if actual != set(schemas):
        raise OperationError("unsupported_database_schema")
    counts = {}
    for schema in schemas:
        for name in sorted(inspect(connection).get_table_names(schema=schema)):
            if not _TABLE_NAME.fullmatch(name):
                raise OperationError("unsupported_database_schema")
            counts[schema + "." + name] = int(
                connection.scalar(text(f'SELECT count(*) FROM "{schema}"."{name}"')) or 0
            )
    if not 1 <= len(counts) <= 128:
        raise OperationError("unsupported_database_schema")
    return counts


def _manifest(data: bytes) -> BackupManifest:
    try:
        result = BackupManifest.model_validate_json(data)
        if result.created_at.tzinfo is None:
            raise ValueError
        for name, scope in SCOPES.items():
            evidence = getattr(result, name)
            if not 1 <= len(evidence.row_counts) <= 128:
                raise ValueError
            for key, count in evidence.row_counts.items():
                schema, table = key.split(".")
                if (
                    schema not in scope.schemas
                    or not _TABLE_NAME.fullmatch(table)
                    or type(count) is not int
                    or count < 0
                ):
                    raise ValueError
            if evidence.row_counts.get("public." + scope.revision_table) != 1:
                raise ValueError
        if (
            result.central.vector_version is not None
            or result.central.schema_revision.startswith("kb_")
            or not result.knowledge.schema_revision.startswith("kb_")
            or result.knowledge.vector_version is None
            or result.knowledge.row_counts.get("deck_kb.contract") != 1
            or not {"deck_kb.documents", "deck_kb.chunks"} <= result.knowledge.row_counts.keys()
        ):
            raise ValueError
        return result
    except (ValidationError, ValueError):
        raise OperationError("invalid_backup_manifest") from None


def read_manifest(directory: int) -> BackupManifest:
    manifest = _manifest(read_file(directory, "manifest.json", maximum=65536))
    for name, scope in SCOPES.items():
        if (
            read_file(directory, scope.checksum, maximum=65)
            != (getattr(manifest, name).sha256 + "\n").encode()
        ):
            raise OperationError("backup_checksum_mismatch")
    return manifest


def _dump_database(
    engine: Engine,
    url: str,
    directory: int,
    scope_name: str,
    dump_command: tuple[str, ...],
    timeout_s: float,
) -> DatabaseEvidence:
    scope = SCOPES[scope_name]
    descriptor = os.open(scope.filename, WRITE_FLAGS, 0o600, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            connection.execute(text("SET TRANSACTION READ ONLY"))
            snapshot = str(connection.scalar(text("SELECT pg_export_snapshot()")))
            counts = table_counts(connection, scope_name)
            revision = str(
                connection.scalar(text(f'SELECT version_num FROM public."{scope.revision_table}"'))
            )
            vector_version = (
                connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                )
                if scope_name == "knowledge"
                else None
            )
            if scope_name == "knowledge" and vector_version is None:
                raise OperationError("knowledge_extension_required")
            run_command(
                (
                    *dump_command,
                    "--format=custom",
                    *("--schema=" + s for s in scope.schemas),
                    *(("--extension=vector",) if scope_name == "knowledge" else ()),
                    "--no-owner",
                    "--no-privileges",
                    "--snapshot",
                    snapshot,
                ),
                env=postgres_environment(url),
                output_fd=descriptor,
                timeout_s=timeout_s,
                maximum_output=MAXIMUM_DUMP_BYTES,
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    reader = os.open(scope.filename, READ_FLAGS, dir_fd=directory)
    try:
        digest, size = digest_file(reader, maximum=MAXIMUM_DUMP_BYTES)
    finally:
        os.close(reader)
    write_file(directory, scope.checksum, (digest + "\n").encode())
    return DatabaseEvidence(
        sha256=digest,
        size_bytes=size,
        schema_revision=revision,
        row_counts=counts,
        vector_version=vector_version,
    )


def create_backup(
    url: str,
    root: Path,
    *,
    knowledge_url: str | None = None,
    dump_command: tuple[str, ...] = ("pg_dump",),
    timeout_s: float = 300,
) -> BackupResult:
    """Publish both dumps atomically; each database has its own consistent snapshot."""
    knowledge_url = require_knowledge_database(url, knowledge_url)
    operation_id = uuid4()
    name, pending = "backup-" + operation_id.hex, ".pending-" + operation_id.hex
    engine = create_engine(url, pool_pre_ping=True)
    knowledge = create_engine(knowledge_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            record_operation(
                session, operation_id=operation_id, operation_type="backup", phase="started"
            )
            session.commit()
        with private_root(root, create=True) as parent:
            with child_directory(parent, pending, create=True) as directory:
                central = _dump_database(engine, url, directory, "central", dump_command, timeout_s)
                kb = _dump_database(
                    knowledge, knowledge_url, directory, "knowledge", dump_command, timeout_s
                )
                manifest = BackupManifest(
                    operation_id=operation_id,
                    created_at=datetime.now(UTC),
                    central=central,
                    knowledge=kb,
                )
                data = manifest.model_dump_json().encode()
                _manifest(data)
                write_file(directory, "manifest.json", data)
                os.fsync(directory)
            os.rename(pending, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        digest = hashlib.sha256(data).hexdigest()
        size = central.size_bytes + kb.size_bytes
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="backup",
                phase="completed",
                sha256=digest,
                counts={"bytes": size},
            )
            session.commit()
        return BackupResult(operation_id, name, digest, size)
    except Exception as error:
        try:
            with private_root(root) as parent:
                if pending in os.listdir(parent):
                    remove_tree(parent, pending)
        except (OperationError, OSError):
            pass
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
        knowledge.dispose()


def verified_sets(
    parent: int, *, on_invalid: Callable[[UUID], None] | None = None
) -> list[tuple[str, datetime]]:
    """Verify sets; strict copy callers fail, retention callers report and skip defects."""
    candidates = []
    for name in os.listdir(parent):
        if not _BACKUP_NAME.fullmatch(name):
            continue
        try:
            with child_directory(parent, name) as directory:
                manifest = read_manifest(directory)
                if name != "backup-" + manifest.operation_id.hex:
                    raise OperationError("invalid_backup_manifest")
                for scope_name, scope in SCOPES.items():
                    evidence = getattr(manifest, scope_name)
                    reader = os.open(scope.filename, READ_FLAGS, dir_fd=directory)
                    try:
                        digest, size = digest_file(reader, maximum=MAXIMUM_DUMP_BYTES)
                    finally:
                        os.close(reader)
                    if (digest, size) != (evidence.sha256, evidence.size_bytes):
                        raise OperationError("backup_checksum_mismatch")
        except (OperationError, OSError):
            if on_invalid is None:
                raise
            # The bounded directory name supplies identity even when the manifest is unsafe.
            # A failed report aborts scanning before retention can remove any verified set.
            on_invalid(UUID(hex=name.removeprefix("backup-")))
            continue
        candidates.append((name, manifest.created_at))
    return candidates


def prune_backups(
    root: Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
    on_invalid: Callable[[UUID], None] | None = None,
) -> int:
    """Prune complete sets older than 14 days only when a newer valid set exists."""
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise OperationError("invalid_clock")
    cutoff = instant - timedelta(days=14)
    with private_root(root) as parent:
        candidates = verified_sets(parent, on_invalid=on_invalid or (lambda _operation_id: None))
        if not any(cutoff <= created <= instant for _, created in candidates):
            return 0
        expired = [name for name, created in candidates if created < cutoff]
        if apply:
            for name in expired:
                remove_tree(parent, name)
        return len(expired)


def backup_summary(result: BackupResult) -> str:
    """Return safe output; the checksum identifies the manifest for both databases."""
    return json.dumps(
        {
            "operation_id": str(result.operation_id),
            "sha256": result.sha256,
            "bytes": result.size_bytes,
            "databases": ["central", "knowledge"],
            "status": "completed",
        }
    )
