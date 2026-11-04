"""Maintenance defaults perform no database or filesystem mutation."""

from __future__ import annotations

import json
import sys

import pytest

from app.operations.main import main


@pytest.mark.parametrize("operation", ["backup", "retention", "restore-probe"])
def test_planning_does_not_require_database_configuration(
    operation: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("DECK_DATABASE_URL", raising=False)
    monkeypatch.delenv("DECK_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["maintenance", operation])
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {"operation": operation, "status": "dry_run"}


def test_restore_refuses_runtime_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["maintenance", "restore-probe", "--apply", "--target", "runtime"]
    )
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unsafe_restore_target"


def test_backup_cli_threads_both_test_databases(monkeypatch, tmp_path, capsys):
    from uuid import uuid4

    from app.operations import main as entrypoint
    from app.operations.backup import BackupResult

    central = "postgresql+psycopg://example/central_test"
    knowledge = "postgresql+psycopg://example/knowledge_test"
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", central)
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", knowledge)
    monkeypatch.setenv("DECK_TEST_BACKUP_DIR", str(tmp_path))
    seen = []

    def create(url, root, *, knowledge_url):
        seen.append((url, knowledge_url, root))
        return BackupResult(uuid4(), "unused", "0" * 64, 2)

    monkeypatch.setattr(entrypoint, "create_backup", create)
    monkeypatch.setattr(entrypoint, "prune_backups", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--target", "test"])
    assert main() == 0
    assert seen == [(central, knowledge, tmp_path)]
    assert json.loads(capsys.readouterr().out)["databases"] == ["central", "knowledge"]


def test_test_backup_never_writes_into_production_backup_dir(monkeypatch, tmp_path, capsys):
    """A test run must leave the production directory and its retention untouched."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.operations import main as entrypoint
    from app.operations.backup import BackupResult
    from tests.unit.test_operations_backup import _backup

    def snapshot(directory):
        return {str(p.relative_to(directory)): p.stat().st_mtime_ns for p in directory.rglob("*")}

    now = datetime.now(UTC)
    production = tmp_path / "prod"
    old = _backup(production, now - timedelta(days=30))
    fresh = _backup(production, now)
    before = snapshot(production)
    monkeypatch.setenv("DECK_BACKUP_DIR", str(production))
    monkeypatch.setenv("DECK_TEST_BACKUP_DIR", str(tmp_path / "test"))
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", "postgresql+psycopg://example/central_test")
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", "postgresql+psycopg://example/knowledge_test")
    seen = []

    def create(url, root, *, knowledge_url):
        seen.append(root)
        root.mkdir(mode=0o700)
        return BackupResult(uuid4(), "unused", "0" * 64, 2)

    monkeypatch.setattr(entrypoint, "create_backup", create)
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--target", "test"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert seen == [tmp_path / "test"]
    assert not seen[0].is_relative_to(production)
    assert old.exists() and fresh.exists()
    assert snapshot(production) == before


@pytest.mark.parametrize(
    "target,expected,databases",
    [
        (
            "test",
            "test",
            {"DECK_TEST_DATABASE_URL": "a_test", "DECK_KB_TEST_DATABASE_URL": "kb_test"},
        ),
        (
            "runtime",
            "prod",
            {"DECK_DATABASE_URL": "central", "DECK_KB_DATABASE_URL": "knowledge"},
        ),
    ],
)
def test_prune_runs_only_on_the_target_directory(
    monkeypatch, tmp_path, capsys, target, expected, databases
):
    from uuid import uuid4

    from app.operations import main as entrypoint
    from app.operations.backup import BackupResult

    for key, value in databases.items():
        monkeypatch.setenv(key, "postgresql+psycopg://example/" + value)
    monkeypatch.setenv("DECK_BACKUP_DIR", str(tmp_path / "prod"))
    monkeypatch.setenv("DECK_TEST_BACKUP_DIR", str(tmp_path / "test"))
    created, pruned = [], []

    def create(url, root, *, knowledge_url):
        created.append(root)
        return BackupResult(uuid4(), "unused", "0" * 64, 2)

    def prune(root, *, apply, on_invalid):
        assert apply is True and callable(on_invalid)
        pruned.append(root)
        return 0

    monkeypatch.setattr(entrypoint, "create_backup", create)
    monkeypatch.setattr(entrypoint, "prune_backups", prune)
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--target", target])
    assert main() == 0
    assert created == pruned == [tmp_path / expected]
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


@pytest.fixture
def completed_backup(monkeypatch, tmp_path):
    from uuid import uuid4

    from app.operations import main as entrypoint
    from app.operations.backup import BackupResult

    monkeypatch.setattr(entrypoint, "get_test_database_url", lambda: "synthetic-central-config")
    monkeypatch.setattr(
        entrypoint, "configured_knowledge_url", lambda **kwargs: "synthetic-knowledge-config"
    )
    monkeypatch.setenv("DECK_TEST_BACKUP_DIR", str(tmp_path))
    result = BackupResult(uuid4(), "unused", "0" * 64, 2)
    monkeypatch.setattr(entrypoint, "create_backup", lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--target", "test"])
    return result


@pytest.mark.parametrize("failure", [OSError("Synthetic private path"), RuntimeError("Synthetic")])
def test_retention_failure_does_not_erase_successful_backup(
    completed_backup, failure, monkeypatch, capsys
):
    from app.operations import main as entrypoint

    def fail_prune(*args, **kwargs):
        raise failure

    reported = []
    monkeypatch.setattr(entrypoint, "prune_backups", fail_prune)
    monkeypatch.setattr(
        entrypoint, "_record_backup_retention_issue", lambda *args: reported.append(args)
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "operation_id": str(completed_backup.operation_id),
        "sha256": completed_backup.sha256,
        "bytes": completed_backup.size_bytes,
        "databases": ["central", "knowledge"],
        "status": "completed",
        "retention": "failed",
    }
    assert len(reported) == 1
    assert reported[0][0] == "synthetic-central-config"
    assert reported[0][2] == "backup_retention_failed"


def test_failed_corruption_audit_preserves_sets_and_backup_success(
    completed_backup, tmp_path, monkeypatch, capsys
):
    from datetime import UTC, datetime, timedelta

    from app.operations import main as entrypoint
    from tests.unit.test_operations_backup import _backup

    now = datetime.now(UTC)
    old = _backup(tmp_path, now - timedelta(days=30))
    fresh = _backup(tmp_path, now)
    corrupt = _backup(tmp_path, now)
    (corrupt / "knowledge.dump").write_bytes(b"corrupted synthetic archive")

    def unavailable(*args):
        raise RuntimeError("Synthetic private audit details")

    monkeypatch.setattr(entrypoint, "_record_backup_retention_issue", unavailable)
    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["operation_id"] == str(completed_backup.operation_id)
    assert summary["status"] == "completed"
    assert summary["retention"] == "audit_failed"
    assert "Synthetic" not in json.dumps(summary)
    assert old.exists() and fresh.exists() and corrupt.exists()


def test_restore_cli_threads_all_four_databases(monkeypatch, capsys):
    from uuid import uuid4

    from app.operations import main as entrypoint

    values = {
        "DECK_TEST_DATABASE_URL": "central_test",
        "DECK_KB_TEST_DATABASE_URL": "knowledge_test",
        "DECK_RESTORE_TEST_DATABASE_URL": "central_target_test",
        "DECK_KB_RESTORE_TEST_DATABASE_URL": "knowledge_target_test",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, "postgresql+psycopg://example/" + value)
    seen = []

    def restore(*args, **kwargs):
        seen.append(kwargs)
        return uuid4()

    monkeypatch.setattr(entrypoint, "restore_backup", restore)
    monkeypatch.setattr(
        sys, "argv", ["maintenance", "restore-probe", "--apply", "--backup-name", "synthetic"]
    )
    assert main() == 0
    assert seen == [
        {
            "target_url": "postgresql+psycopg://example/central_target_test",
            "audit_url": "postgresql+psycopg://example/central_test",
            "knowledge_target_url": "postgresql+psycopg://example/knowledge_target_test",
            "knowledge_source_url": "postgresql+psycopg://example/knowledge_test",
        }
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
