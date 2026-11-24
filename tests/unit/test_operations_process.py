"""Maintenance commands are bounded and do not expose child diagnostics."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from app.operations.errors import OperationError, ProcessExitError
from app.operations.process import run_command


def test_dump_stream_is_exact(tmp_path: Path) -> None:
    target = tmp_path / "dump"
    with target.open("wb") as stream:
        run_command(
            (sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'abc'*100000)"),
            env={"PATH": os.defpath},
            output_fd=stream.fileno(),
            maximum_output=400000,
        )
    assert target.read_bytes() == b"abc" * 100000


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ("import sys;sys.stdout.write('x'*1000)", "process_output_limit"),
        ("import time;time.sleep(30)", "process_timeout"),
        ("import sys;sys.stderr.write('private-diagnostic');sys.exit(2)", "process_failed"),
        ("import os,time;os.fork();time.sleep(30)", "process_timeout"),
    ],
)
def test_process_limits_and_safe_errors(script: str, code: str) -> None:
    with pytest.raises(OperationError) as error:
        run_command(
            (sys.executable, "-c", script),
            env={"PATH": os.defpath},
            timeout_s=0.3,
            maximum_output=100,
        )
    assert error.value.code == code
    assert "private-diagnostic" not in str(error.value)


@pytest.mark.parametrize(
    ("script", "returncode"),
    [
        ("import sys;sys.exit(3)", 3),
        ("import os,signal;os.kill(os.getpid(),signal.SIGTERM)", -signal.SIGTERM),
    ],
)
def test_failed_exit_keeps_the_return_code(script: str, returncode: int) -> None:
    with pytest.raises(ProcessExitError) as error:
        run_command((sys.executable, "-c", script), env={"PATH": os.defpath}, timeout_s=20)
    assert error.value.code == "process_failed"
    assert error.value.returncode == returncode
