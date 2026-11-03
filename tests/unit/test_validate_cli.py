"""Tests for scripts/validate.py CLI exit codes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).parent / "examples"
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate.py"


def _run(kind: str, path: Path) -> int:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), kind, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


def test_cli_task_valid_exit_zero() -> None:
    assert _run("task", EXAMPLES / "task_valid_01.json") == 0


def test_cli_task_invalid_exit_one() -> None:
    assert _run("task", EXAMPLES / "task_invalid_01_missing_data_class.json") == 1


def test_cli_result_valid_exit_zero() -> None:
    assert _run("result", EXAMPLES / "result_valid_01.json") == 0


def test_cli_result_invalid_exit_one() -> None:
    assert _run("result", EXAMPLES / "result_invalid_01_failed_without_error.json") == 1
