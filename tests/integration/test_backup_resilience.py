"""Completed backups survive corrupt older sets and retention failures, with durable audit."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.operations import backup
from app.operations import main as entrypoint
from app.operations.errors import OperationError
from app.operations.files import private_root
from app.operations.models import OperationEvent
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.unit.test_operations_backup import _backup


@pytest.fixture
def backup_cli(monkeypatch, tmp_path, kb_engine):
    """Use real isolated snapshot/audit transactions and an in-process fake pg_dump."""

    def fake_dump(command, *, env, output_fd, timeout_s, maximum_output):
        assert command[0] == "pg_dump"
        assert "--format=custom" in command
        os.write(output_fd, b"synthetic custom dump")

    monkeypatch.setattr(backup, "run_command", fake_dump)
    root = tmp_path / "backups"
    monkeypatch.setenv("DECK_TEST_BACKUP_DIR", str(root))
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--target", "test"])
    return root


@pytest.mark.parametrize("corruption", ["knowledge_dump", "manifest", "missing_dump", "symlink"])
def test_backup_succeeds_and_audits_corrupt_set(db_session, backup_cli, capsys, corruption):
    now = datetime.now(UTC)
    expired = _backup(backup_cli, now - timedelta(days=30))
    corrupt = _backup(backup_cli, now - timedelta(days=40))
    corrupt_id = UUID(hex=corrupt.name.removeprefix("backup-"))
    artifact = corrupt / "knowledge.dump"
    if corruption == "knowledge_dump":
        artifact.write_bytes(b"corrupted synthetic archive")
    elif corruption == "manifest":
        (corrupt / "manifest.json").write_text("invalid synthetic manifest")
    else:
        artifact.unlink()
        if corruption == "symlink":
            artifact.symlink_to(expired / "knowledge.dump")

    assert entrypoint.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "completed"
    assert summary["retention"] == "partial_failure"
    operation_id = UUID(summary["operation_id"])
    assert (backup_cli / ("backup-" + operation_id.hex)).is_dir()
    assert corrupt.exists()
    assert not expired.exists()
    with private_root(backup_cli) as parent:
        reported = []
        assert [name for name, _ in backup.verified_sets(parent, on_invalid=reported.append)] == [
            "backup-" + operation_id.hex
        ]
        assert reported == [corrupt_id]
    events = db_session.scalars(select(OperationEvent).order_by(OperationEvent.id)).all()
    assert [(event.operation_type, event.phase) for event in events] == [
        ("backup", "started"),
        ("backup", "completed"),
        ("retention", "failed"),
    ]
    assert events[-1].operation_id == corrupt_id
    assert events[-1].detail == {"reason": "invalid_backup_set", "backups": 1}


def test_backup_succeeds_and_audits_retention_failure(db_session, backup_cli, monkeypatch, capsys):
    def fail_prune(*args, **kwargs):
        raise OperationError("unsafe_path")

    monkeypatch.setattr(entrypoint, "prune_backups", fail_prune)
    assert entrypoint.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "completed"
    assert summary["retention"] == "failed"
    operation_id = UUID(summary["operation_id"])
    assert (backup_cli / ("backup-" + operation_id.hex)).is_dir()
    events = db_session.scalars(select(OperationEvent).order_by(OperationEvent.id)).all()
    assert [(event.operation_type, event.phase) for event in events] == [
        ("backup", "started"),
        ("backup", "completed"),
        ("retention", "failed"),
    ]
    assert events[-1].detail == {"reason": "backup_retention_failed"}


def test_restic_failure_keeps_overall_backup_failed(db_session, backup_cli, monkeypatch, capsys):
    from app.operations import restic
    from app.operations.process import run_command

    runs = backup_cli.parent / "runs"
    configuration = backup_cli.parent / "configuration"
    runs.mkdir(mode=0o700)
    configuration.mkdir(mode=0o700)
    credential = configuration / "credential"
    credential.write_text("[[REDACTED_TEST_CREDENTIAL]]")
    credential.chmod(0o600)
    monkeypatch.setenv("DECK_RUNS_DIR", str(runs))
    monkeypatch.setenv("RESTIC_REPOSITORY", "synthetic-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(credential))
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--apply", "--restic"])

    def failed_restic(command, **kwargs):
        assert command[0] == "restic"
        run_command((sys.executable, "-c", "raise SystemExit(23)"), **kwargs)

    monkeypatch.setattr(restic, "run_command", failed_restic)
    monkeypatch.setattr(
        entrypoint, "prune_backups", lambda *a, **k: pytest.fail("Copy failed before retention")
    )
    assert entrypoint.main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "reason": "process_failed"}
    with private_root(backup_cli) as parent:
        assert len(backup.verified_sets(parent)) == 1
    events = db_session.scalars(select(OperationEvent).order_by(OperationEvent.id)).all()
    assert [(event.operation_type, event.phase) for event in events] == [
        ("backup", "started"),
        ("backup", "completed"),
        ("backup", "started"),
        ("backup", "failed"),
    ]
    assert events[-1].detail == {"reason": "restic_failed"}
