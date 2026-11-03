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
