"""Bounded subprocess transport; this is process lifecycle control, not a sandbox."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ProcessStatus = Literal[
    "completed", "timeout", "cancelled", "output_limit", "spawn_failed", "internal"
]
_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ProcessResult:
    status: ProcessStatus
    returncode: int | None
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    wall_seconds: float


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the private session's group and reap the immediate child."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    # Descendants may still own pipes after the leader exits or ignores TERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1.0)


def run_process(
    command: Sequence[str],
    *,
    input_bytes: bytes,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    cancel_requested: Callable[[], bool],
    max_stdout_bytes: int = 4 * 1024 * 1024,
    max_stderr_bytes: int = 64 * 1024,
    poll_interval_s: float = 0.02,
) -> ProcessResult:
    """Send exact bytes once, drain both pipes, and stop promptly on a bounded failure.

    Only the explicit environment is passed. Callers must select trusted programs
    and supply their environment allowlist; this helper does not isolate files or
    network access. No output is included in errors or dataclass representations.
    """
    if (
        isinstance(command, (str, bytes))
        or not 1 <= len(command) <= 64
        or any(not isinstance(arg, str) or "\0" in arg for arg in command)
        or not Path(command[0]).is_absolute()
        or sum(len(arg) for arg in command) > 65536
        or not isinstance(input_bytes, bytes)
        or len(input_bytes) > _MAX_BYTES
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or not 0 < timeout_s <= 7200
        or isinstance(poll_interval_s, bool)
        or not isinstance(poll_interval_s, (int, float))
        or not math.isfinite(poll_interval_s)
        or not 0 < poll_interval_s <= 1
        or any(
            type(limit) is not int or not 0 < limit <= _MAX_BYTES
            for limit in (max_stdout_bytes, max_stderr_bytes)
        )
        or any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\0" in key
            or not isinstance(value, str)
            or "\0" in value
            for key, value in env.items()
        )
    ):
        raise ValueError("Invalid process configuration")
    started = time.monotonic()

    def result(
        status: ProcessStatus, code: int | None = None, stdout: bytes = b"", stderr: bytes = b""
    ) -> ProcessResult:
        return ProcessResult(status, code, stdout, stderr, max(0.0, time.monotonic() - started))

    try:
        if cancel_requested():
            return result("cancelled")
    except Exception:
        return result("internal")
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return result("spawn_failed")

    selector = selectors.DefaultSelector()
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
    status: ProcessStatus = "completed"
    offset = 0
    try:
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        if input_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        while True:
            if cancel_requested():
                status = "cancelled"
                break
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                status = "timeout"
                break
            # An exited leader must not leave background descendants holding pipes.
            if process.poll() is not None:
                _stop_group(process)
                if not selector.get_map():
                    break
            for key, _ in selector.select(min(poll_interval_s, remaining)):
                registered_stream = key.fileobj
                name = key.data
                if name == "stdin":
                    try:
                        written = os.write(key.fd, input_bytes[offset : offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        status = "internal"
                        break
                    offset += written
                    if offset >= len(input_bytes):
                        selector.unregister(registered_stream)
                        process.stdin.close()
                    continue
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(registered_stream)
                    if name == "stdout":
                        process.stdout.close()
                    else:
                        process.stderr.close()
                    continue
                if len(captured[name]) + len(chunk) > limits[name]:
                    status = "output_limit"
                    break
                captured[name].extend(chunk)
            if status != "completed":
                break
        if status == "completed":
            process.wait(timeout=1)
    except Exception:
        status = "internal"
    finally:
        try:
            _stop_group(process)
        except (OSError, subprocess.TimeoutExpired):
            status = "internal"
        selector.close()
        for cleanup_pipe in (process.stdin, process.stdout, process.stderr):
            if cleanup_pipe is not None:
                cleanup_pipe.close()
    if status != "completed":
        return result(status, process.returncode)
    return result(status, process.returncode, bytes(captured["stdout"]), bytes(captured["stderr"]))
