"""Update failure recovers a disposable Git checkout and persists its audit event."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.operations import service
from app.operations.errors import OperationError
from app.operations.models import OperationEvent
from tests.integration.helpers import require_test_database_url


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        capture_output=True,
        check=True,
        timeout=10,
        text=True,
    ).stdout.strip()


def test_failed_update_restores_checkout_services_and_records_audit(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture")
    _git(repository, "config", "commit.gpgsign", "false")
    content = repository / "version.txt"
    content.write_text("previous\n")
    _git(repository, "add", "version.txt")
    _git(repository, "commit", "-m", "Previous fixture")
    previous = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "-b", "proposed")
    content.write_text("proposed\n")
    _git(repository, "commit", "-am", "Proposed fixture")
    proposed = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", proposed)
    _git(repository, "checkout", "main")

    existing = set(db_session.scalars(select(OperationEvent.operation_id)))
    active = ("deck-api.service", "deck-runner@codex.service")
    calls = []
    execute = service.run_command

    def simulated_command(command, **kwargs):
        calls.append(command)
        if command[0] == "git":
            if "fetch" not in command:
                execute(command, **kwargs)
        elif command[0] in {"systemctl", "uv"}:
            return
        else:
            assert content.read_text() == "proposed\n"
            assert _git(repository, "rev-parse", "HEAD") == proposed
            raise OperationError("simulated_migration_failure")

    monkeypatch.setattr(service, "run_command", simulated_command)
    monkeypatch.setattr(service, "create_backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "service_status",
        lambda: {unit: "active" if unit in active else "inactive" for unit in service._SERVICES},
    )
    monkeypatch.setenv("DECK_DATABASE_URL", require_test_database_url())
    monkeypatch.setattr(service, "configured_knowledge_url", lambda **kwargs: "synthetic-kb")
    monkeypatch.setattr(
        sys, "argv", ["service", "update", "--apply", "--repository", str(repository)]
    )

    assert service.main() == 78
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "reason": "update_failed"}
    assert content.read_text() == "previous\n"
    assert _git(repository, "rev-parse", "HEAD") == previous
    checkout = ("git", "-C", str(repository), "checkout", "--detach", previous)
    recovery_calls = calls[calls.index(checkout) + 1 :]
    assert recovery_calls == [
        ("uv", "sync", "--frozen", "--project", str(repository)),
        *(("systemctl", "--user", "start", unit) for unit in active),
    ]
    events = list(
        db_session.scalars(
            select(OperationEvent)
            .where(OperationEvent.operation_id.not_in(existing))
            .order_by(OperationEvent.id)
        )
    )
    assert [(event.operation_type, event.phase) for event in events] == [
        ("update", "started"),
        ("update", "failed"),
    ]
    assert events[0].operation_id == events[1].operation_id
    assert events[1].detail == {"reason": "update_failed"}
