"""Loadtest CLI boundaries: argument limits and test-database-only guard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "loadtest.py"


def _run(
    args: list[str], env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    environment.pop("DECK_TEST_DATABASE_URL", None)
    environment.pop("DECK_DATABASE_URL", None)
    if env_overrides:
        environment.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=_REPO_ROOT,
        timeout=60,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--tasks", "0"],
        ["--tasks", "501"],
        ["--runners", "0"],
        ["--runners", "9"],
        ["--timeout-s", "5"],
        ["--timeout-s", "1801"],
        ["--worker", "codex"],
    ],
    ids=[
        "tasks-zero",
        "tasks-too-many",
        "runners-zero",
        "runners-too-many",
        "timeout-too-short",
        "timeout-too-long",
        "codex-excluded",
    ],
)
def test_invalid_arguments_exit_before_any_database_access(args: list[str]) -> None:
    result = _run(args)
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_missing_test_database_url_is_rejected() -> None:
    result = _run(["--tasks", "1", "--runners", "1"])
    assert result.returncode == 2
    assert "DECK_TEST_DATABASE_URL" in result.stderr


def test_non_test_database_name_is_rejected() -> None:
    result = _run(
        ["--tasks", "1", "--runners", "1"],
        {"DECK_TEST_DATABASE_URL": "postgresql+psycopg://postgres@localhost/controldeck"},
    )
    assert result.returncode == 2
    assert "_test" in result.stderr


def test_worker_process_requires_private_arguments() -> None:
    result = _run(["--worker-process", "ollama"])
    assert result.returncode == 2
    assert "--runs-dir" in result.stderr
