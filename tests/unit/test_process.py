"""Bounded subprocesses preserve input and cannot outlive cancellation or deadlines."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from app.workers.process import run_process


def _run(tmp_path: Path, source: str, **overrides):
    arguments = {
        "input_bytes": b"",
        "cwd": tmp_path,
        "env": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        "timeout_s": 3.0,
        "cancel_requested": lambda: False,
    }
    arguments.update(overrides)
    return run_process([sys.executable, "-c", source], **arguments)


def _running(pid: int) -> bool:
    """A terminated orphan may briefly remain a zombie under a container's init."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != "Z"


def _assert_child_stopped(pid_path: Path) -> None:
    assert pid_path.exists(), "The fake child must start before cleanup is exercised"
    pid = int(pid_path.read_text())
    until = time.monotonic() + 2
    while _running(pid) and time.monotonic() < until:
        time.sleep(0.01)
    if _running(pid):
        # A failed regression must not itself leave a synthetic orphan behind.
        os.kill(pid, signal.SIGKILL)
        pytest.fail("The subprocess group left a running descendant")


def test_input_bytes_are_sent_exactly_without_shell_or_added_newline(tmp_path):
    payload = b"\xef\xbb\xbfline one\r\nline two\x00tail"
    result = _run(
        tmp_path,
        "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        input_bytes=payload,
    )
    assert result.status == "completed"
    assert result.returncode == 0
    assert result.stdout == payload
    assert result.stderr == b""
    assert 0 <= result.wall_seconds < 3


def test_process_receives_only_the_explicit_environment(tmp_path, monkeypatch):
    for name in ("DECK_OWNER_TOKEN", "DECK_DATABASE_URL", "OLLAMA_HOST", "HTTPS_PROXY", "HOME"):
        monkeypatch.setenv(name, "synthetic-parent-value")
    result = _run(
        tmp_path,
        "import json, os; print(json.dumps({key: os.getenv(key) for key in "
        "['DECK_OWNER_TOKEN','DECK_DATABASE_URL','OLLAMA_HOST','HTTPS_PROXY','HOME','EXPLICIT']}))",
        env={"EXPLICIT": "synthetic-child-value"},
    )
    assert result.status == "completed"
    assert json.loads(result.stdout) == {
        "DECK_OWNER_TOKEN": None,
        "DECK_DATABASE_URL": None,
        "OLLAMA_HOST": None,
        "HTTPS_PROXY": None,
        "HOME": None,
        "EXPLICIT": "synthetic-child-value",
    }


def test_stdin_and_both_output_pipes_make_progress_without_deadlock(tmp_path):
    size = 192 * 1024
    source = (
        "import sys\n"
        f"sys.stdout.buffer.write(b'O' * {size}); sys.stdout.flush()\n"
        f"sys.stderr.buffer.write(b'E' * {size}); sys.stderr.flush()\n"
        "data = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(str(len(data)).encode())\n"
    )
    result = _run(
        tmp_path,
        source,
        input_bytes=b"I" * size,
        max_stdout_bytes=size + 64,
        max_stderr_bytes=size + 64,
    )
    assert result.status == "completed"
    assert result.stdout == b"O" * size + str(size).encode()
    assert result.stderr == b"E" * size


def test_closing_stdin_before_full_payload_delivery_cannot_report_success(tmp_path):
    result = _run(
        tmp_path,
        "import os, sys, time; os.close(0); "
        "sys.stdout.write('synthetic-success'); sys.stdout.flush(); time.sleep(0.05)",
        input_bytes=b"I" * (1024 * 1024),
    )
    assert result.status == "internal"
    assert result.stdout == result.stderr == b""


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_each_output_stream_has_an_independent_hard_limit(tmp_path, stream):
    result = _run(
        tmp_path,
        f"import sys; sys.{stream}.buffer.write(b'X' * 200000); sys.{stream}.flush()",
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
    )
    assert result.status == "output_limit"
    assert len(result.stdout) <= 1024
    assert len(result.stderr) <= 1024


def test_output_bytes_are_not_exposed_by_result_repr(tmp_path):
    marker = "synthetic-private-output"
    result = _run(
        tmp_path,
        f"import sys; print({marker!r}); print({marker!r}, file=sys.stderr)",
    )
    assert marker.encode() in result.stdout
    assert marker.encode() in result.stderr
    assert marker not in repr(result)


def test_nonzero_exit_is_returned_without_retry_or_echoing_failure(tmp_path):
    result = _run(
        tmp_path,
        "from pathlib import Path; import sys\n"
        "path = Path('calls'); path.write_text(path.read_text() + 'x' if path.exists() else 'x')\n"
        "print('synthetic-error-output', file=sys.stderr); sys.exit(7)",
    )
    assert result.status == "completed"
    assert result.returncode == 7
    assert (tmp_path / "calls").read_text() == "x"


def test_deadline_terminates_and_reaps_the_process(tmp_path):
    started = time.monotonic()
    result = _run(tmp_path, "import time; time.sleep(30)", timeout_s=0.15)
    assert result.status == "timeout"
    assert result.returncode is not None
    assert time.monotonic() - started < 3


def test_cancelled_before_start_does_not_launch_a_process(tmp_path, monkeypatch):
    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("An already cancelled request must not spawn")

    monkeypatch.setattr("subprocess.Popen", forbidden_spawn)
    result = _run(
        tmp_path, "raise AssertionError('must not execute')", cancel_requested=lambda: True
    )
    assert result.status == "cancelled"
    assert result.returncode is None


def test_cancellation_during_execution_is_bounded(tmp_path):
    marker = tmp_path / "started"
    result = _run(
        tmp_path,
        "from pathlib import Path; import time; Path('started').write_text('yes'); time.sleep(30)",
        cancel_requested=marker.exists,
    )
    assert marker.exists()
    assert result.status == "cancelled"
    assert result.returncode is not None
    assert result.wall_seconds < 3


def test_missing_executable_has_a_safe_failure_result(tmp_path):
    path = tmp_path / "missing-executable"
    result = run_process(
        [str(path)],
        input_bytes=b"",
        cwd=tmp_path,
        env={},
        timeout_s=1,
        cancel_requested=lambda: False,
    )
    assert result.status == "spawn_failed"
    assert result.returncode is None
    assert result.stdout == result.stderr == b""
    assert str(path) not in repr(result)


@pytest.mark.parametrize("leader_exits", [False, True])
def test_descendants_holding_pipes_are_terminated_with_the_group(tmp_path, leader_exits):
    child = (
        "import os, time; from pathlib import Path; "
        "Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
    )
    source = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        + ("time.sleep(0.1)\n" if leader_exits else "time.sleep(30)\n")
    )
    result = _run(tmp_path, source, timeout_s=0.5)
    assert result.status in ("completed", "timeout")
    assert result.wall_seconds < 3
    _assert_child_stopped(tmp_path / "child.pid")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected_without_execution(tmp_path, timeout):
    with pytest.raises(ValueError):
        _run(tmp_path, "raise AssertionError('must not execute')", timeout_s=timeout)


@pytest.mark.parametrize("limit", [0, -1, True])
@pytest.mark.parametrize("field", ["max_stdout_bytes", "max_stderr_bytes"])
def test_invalid_output_limit_is_rejected(tmp_path, field, limit):
    with pytest.raises(ValueError):
        _run(tmp_path, "raise AssertionError('must not execute')", **{field: limit})


@pytest.mark.parametrize("command", [[], ["relative-executable"], [sys.executable, "bad\x00arg"]])
def test_invalid_command_is_rejected(tmp_path, command):
    with pytest.raises(ValueError):
        run_process(
            command,
            input_bytes=b"",
            cwd=tmp_path,
            env={},
            timeout_s=1,
            cancel_requested=lambda: False,
        )
