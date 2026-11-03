"""Backup manifests, retention boundaries and explicit restore safeguards."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.operations.audit import record_operation
from app.operations.backup import BackupManifest, postgres_environment, prune_backups
from app.operations.errors import OperationError
from app.operations.files import child_directory, private_root, write_file
from app.operations.restore import validate_restore_databases


def _backup(root: Path, created: datetime) -> Path:
    operation_id = uuid4()
    data = b"test-only-dump"
    manifest = BackupManifest(
        operation_id=operation_id,
        created_at=created,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        schema_revision="20251103_0015",
        row_counts={"tasks": 2},
    )
    name = "backup-" + operation_id.hex
    with (
        private_root(root, create=True) as parent,
        child_directory(parent, name, create=True) as directory,
    ):
        write_file(directory, "database.dump", data)
        write_file(directory, "manifest.json", manifest.model_dump_json().encode())
        write_file(directory, "dump.sha256", (manifest.sha256 + "\n").encode())
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
        "postgresql://example/target_test", "postgresql://example/source_test"
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
