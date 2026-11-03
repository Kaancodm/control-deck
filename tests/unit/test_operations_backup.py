"""Backup manifests, retention boundaries and explicit restore safeguards."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.operations import backup
from app.operations.audit import record_operation
from app.operations.backup import (
    BackupManifest,
    DatabaseEvidence,
    postgres_environment,
    prune_backups,
)
from app.operations.errors import OperationError
from app.operations.files import child_directory, private_root, write_file
from app.operations.restore import validate_restore_databases


def test_backup_requires_explicit_knowledge_database_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "create_engine", lambda *a, **k: pytest.fail("No connection"))
    with pytest.raises(OperationError) as error:
        backup.create_backup("postgresql://example/source_test", tmp_path / "backups")
    assert error.value.code == "knowledge_database_required"
    assert not (tmp_path / "backups").exists()


def _backup(root: Path, created: datetime) -> Path:
    operation_id = uuid4()
    data = b"test-only-dump"
    manifest = BackupManifest(
        operation_id=operation_id,
        created_at=created,
        central=DatabaseEvidence(
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            schema_revision="20251103_0015",
            row_counts={"public.tasks": 2, "public.alembic_version": 1},
        ),
        knowledge=DatabaseEvidence(
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            schema_revision="kb_0001",
            vector_version="0.8.1",
            row_counts={
                "public.alembic_kb_version": 1,
                "deck_kb.contract": 1,
                "deck_kb.documents": 0,
                "deck_kb.chunks": 0,
            },
        ),
    )
    name = "backup-" + operation_id.hex
    with (
        private_root(root, create=True) as parent,
        child_directory(parent, name, create=True) as directory,
    ):
        write_file(directory, "database.dump", data)
        write_file(directory, "knowledge.dump", data)
        write_file(directory, "knowledge.sha256", (manifest.knowledge.sha256 + "\n").encode())
        write_file(directory, "manifest.json", manifest.model_dump_json().encode())
        write_file(directory, "dump.sha256", (manifest.central.sha256 + "\n").encode())
    return root / name


def test_backup_retention_boundary_and_dry_run(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "backups"
    old = _backup(root, now - timedelta(days=14, microseconds=1))
    boundary = _backup(root, now - timedelta(days=14))
    fresh = _backup(root, now)
    assert prune_backups(root, now=now) == 1
    assert old.exists()
    assert prune_backups(root, now=now, apply=True) == 1
    assert not old.exists() and boundary.exists() and fresh.exists()


def test_retention_keeps_last_backup_without_recent_verified_set(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "backups"
    old = _backup(root, now - timedelta(days=30))
    assert prune_backups(root, now=now, apply=True) == 0
    assert old.exists()


@pytest.mark.parametrize("corruption", ["checksum", "contents", "symlink", "manifest"])
def test_corrupt_backups_prevent_retention(tmp_path: Path, corruption: str) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "backups"
    old = _backup(root, now - timedelta(days=30))
    fresh = _backup(root, now)
    if corruption == "checksum":
        (fresh / "dump.sha256").write_text("0" * 64 + "\n")
    elif corruption == "contents":
        (fresh / "database.dump").write_bytes(b"changed")
    elif corruption == "symlink":
        (fresh / "database.dump").unlink()
        (fresh / "database.dump").symlink_to(old / "database.dump")
    else:
        (fresh / "manifest.json").write_text('{"untrusted":true}')
    with pytest.raises((OperationError, OSError)):
        prune_backups(root, now=now, apply=True)
    assert old.exists()


@pytest.mark.parametrize(
    "target,audit",
    [
        ("postgresql://example/runtime", "postgresql://example/source_test"),
        ("postgresql://example/source_test", "postgresql://example/source_test"),
        ("postgresql://example/target_test", "postgresql://other/source_test"),
        ("postgresql://example/target_test", "postgresql://example/runtime"),
        ("sqlite:///target_test", "postgresql://example/source_test"),
        ("", ""),
    ],
)
def test_restore_refuses_unsafe_databases_before_connecting(target: str, audit: str) -> None:
    with pytest.raises(OperationError):
        validate_restore_databases(target, audit)


def test_restore_accepts_distinct_explicit_test_databases() -> None:
    validate_restore_databases(
        "postgresql://example/target_test",
        "postgresql://example/source_test",
        knowledge_target_url="postgresql://example/kb_target_test",
        knowledge_source_url="postgresql://example/kb_source_test",
    )


def test_postgres_child_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "fake-value-never-forwarded")
    environment = postgres_environment(
        "postgresql+psycopg://worker:fake-password@example/source_test"
    )
    assert environment["PGPASSWORD"] == "fake-password"
    assert environment["PGDATABASE"] == "source_test"
    assert environment["PATH"] == os.defpath
    assert "DECK_OWNER_TOKEN" not in environment


@pytest.mark.parametrize(
    "detail",
    [
        {"reason": "raw/private/path"},
        {"reason": "secret\nvalue"},
        {"sha256": "invalid"},
        {"counts": {"files": -1}},
        {"counts": {"files": True}},
        {"counts": {"raw_output": 1}},
    ],
)
def test_operation_metadata_refuses_unbounded_details(detail: dict) -> None:
    with pytest.raises(ValueError):
        record_operation(
            None, operation_id=uuid4(), operation_type="backup", phase="started", **detail
        )


@pytest.mark.parametrize(
    "target,source",
    [
        ("source_test", "kb_source_test"),
        ("target_test", "kb_source_test"),
        ("kb_source_test", "kb_source_test"),
        ("kb_target_test", "source_test"),
        ("runtime", "kb_source_test"),
    ],
)
def test_restore_rejects_aliasing_or_unsafe_knowledge_databases(target, source):
    with pytest.raises(OperationError) as error:
        validate_restore_databases(
            "postgresql://example/target_test",
            "postgresql://example/source_test",
            knowledge_target_url="postgresql://example/" + target,
            knowledge_source_url="postgresql://example/" + source,
        )
    assert error.value.code == "unsafe_restore_target"


@pytest.mark.parametrize("artifact", ["knowledge.dump", "knowledge.sha256"])
def test_missing_knowledge_artifact_blocks_retention(tmp_path, artifact):
    now = datetime.now(UTC)
    old = _backup(tmp_path, now - timedelta(days=30))
    fresh = _backup(tmp_path, now)
    (fresh / artifact).unlink()
    with pytest.raises((OperationError, OSError)):
        prune_backups(tmp_path, now=now, apply=True)
    assert old.exists()


def test_legacy_central_only_manifest_is_never_a_complete_set(tmp_path):
    import json

    now = datetime.now(UTC)
    old = _backup(tmp_path, now - timedelta(days=30))
    fresh = _backup(tmp_path, now)
    manifest = json.loads((fresh / "manifest.json").read_text())
    legacy = {
        "version": 1,
        "operation_id": manifest["operation_id"],
        "created_at": manifest["created_at"],
        **manifest["central"],
    }
    (fresh / "manifest.json").write_text(json.dumps(legacy))
    with pytest.raises(OperationError) as error:
        prune_backups(tmp_path, now=now, apply=True)
    assert error.value.code == "invalid_backup_manifest"
    assert old.exists()


def test_runtime_knowledge_configuration_never_uses_test_fallback(monkeypatch):
    monkeypatch.delenv("DECK_KB_DATABASE_URL", raising=False)
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", "postgresql+psycopg://example/kb_test")
    with pytest.raises(OperationError) as error:
        backup.configured_knowledge_url(for_tests=False)
    assert error.value.code == "knowledge_database_required"
