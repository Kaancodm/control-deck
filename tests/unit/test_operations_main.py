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
    monkeypatch.setenv("DECK_BACKUP_DIR", str(tmp_path))
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
