"""Bounded subprocesses preserve input and cannot outlive cancellation or deadlines."""

from __future__ import annotations

import json
import os
import select
import signal
import sys
import time
from pathlib import Path

import pytest

from app.workers.process import run_process

# Hang guard for tests whose stop is driven by an event, never by this deadline.
_SAFETY_TIMEOUT_S = 10.0
# How long the fake descendant gets to publish a complete, valid pid before the test fails.
_CHILD_START_TIMEOUT_S = 5.0


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


def _read_valid_pid(pid_path: Path) -> int | None:
    """Return the pid once ``pid_path`` holds a complete pid of a live process, else None."""
    try:
        text = pid_path.read_text()
    except FileNotFoundError:
        return None
    if not text.strip().isdigit():
        return None
    pid = int(text)
    if pid <= 1:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    return pid


class _DescendantProbe:
    """``cancel_requested`` hook that confirms the fake descendant before any cleanup.

    ``run_process`` polls the hook on every loop iteration. The probe waits for a complete
    pid file naming a live process, records that pid, then releases the leader through the
    ``go`` file. Only after that confirmation may cleanup start: either the released leader
    exits, or the probe requests cancellation. If no valid pid appears within ``timeout_s``
    the probe cancels the run so the test fails with a clear message instead of waiting for
    the deadline of ``run_process``.
    """

    def __init__(self, cwd: Path, *, cancel_after_confirmation: bool, timeout_s: float):
        self.pid_path = cwd / "child.pid"
        self.go_path = cwd / "go"
        self.cancel_after_confirmation = cancel_after_confirmation
        self.deadline = time.monotonic() + timeout_s
        self.pid: int | None = None
        self.timed_out = False

    def __call__(self) -> bool:
        if self.pid is None:
            self.pid = _read_valid_pid(self.pid_path)
            if self.pid is None:
                if time.monotonic() >= self.deadline:
                    self.timed_out = True
                    return True
                return False
            self.go_path.write_text("go")
        return self.cancel_after_confirmation


def _wait_for_exit(pid: int, timeout_s: float) -> bool:
    """Return True once ``pid`` has terminated; False if it is still alive after ``timeout_s``.

    The descendant is reparented to the container's init after the leader dies, so it
    cannot be reaped with ``waitpid`` here. A pidfd becomes readable on termination,
    including while the process is still a zombie awaiting its new parent. Probing
    ``/proc/<pid>/stat`` instead raced that reaping: the entry can open and then
    fail with ``ESRCH`` once init collects the zombie.
    """
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        readable, _, _ = select.select([pidfd], [], [], timeout_s)
    finally:
        os.close(pidfd)
    return bool(readable)


def _assert_child_stopped(pid: int) -> None:
    if not _wait_for_exit(pid, timeout_s=_SAFETY_TIMEOUT_S):
        # A failed regression must not itself leave a synthetic orphan behind.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
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


@pytest.mark.parametrize("child_start_delay_s", [0.0, 0.5], ids=["prompt-start", "delayed-start"])
@pytest.mark.parametrize("leader_exits", [False, True])
def test_descendants_holding_pipes_are_terminated_with_the_group(
    tmp_path, leader_exits, child_start_delay_s
):
    # Ordering is enforced by files in cwd, never by sleeps or deadlines:
    #   1. the descendant publishes its pid atomically (child.pid.tmp -> child.pid),
    #   2. the probe confirms a complete pid of a live process and writes ``go``,
    #   3. only then does cleanup start: the leader exits (leader_exits) or the probe cancels.
    # ``delayed-start`` stands in for a slow interpreter start under CI load; it must pass too.
    child = (
        "import os, time; from pathlib import Path; "
        f"time.sleep({child_start_delay_s!r}); "
        "Path('child.pid.tmp').write_text(str(os.getpid())); "
        "os.replace('child.pid.tmp', 'child.pid'); time.sleep(30)"
    )
    source = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "while not Path('go').exists():\n"
        "    time.sleep(0.01)\n" + ("" if leader_exits else "time.sleep(30)\n")
    )
    probe = _DescendantProbe(
        tmp_path, cancel_after_confirmation=not leader_exits, timeout_s=_CHILD_START_TIMEOUT_S
    )
    result = _run(tmp_path, source, timeout_s=_SAFETY_TIMEOUT_S, cancel_requested=probe)
    if probe.timed_out:
        pytest.fail(
            f"The fake descendant published no valid pid within {_CHILD_START_TIMEOUT_S} s; "
            "cleanup was not exercised"
        )
    assert probe.pid is not None
    if leader_exits:
        # The released leader exited; cleanup must follow from that exit alone.
        assert result.status == "completed"
        assert result.returncode == 0
    else:
        assert result.status == "cancelled"
        assert result.returncode is not None
    _assert_child_stopped(probe.pid)


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
