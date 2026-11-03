"""Bounded maintenance subprocesses with no shell or captured diagnostic output."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app.operations.errors import OperationError


def run_command(
    command: Sequence[str],
    *,
    env: dict[str, str],
    timeout_s: float = 300,
    input_fd: int | None = None,
    output_fd: int | None = None,
    maximum_output: int = 1024 * 1024 * 1024,
    cancelled: Callable[[], bool] | None = None,
    cwd: Path | None = None,
    pass_fds: tuple[int, ...] = (),
) -> None:
    """Bound process lifetime and streamed bytes; kill the complete subprocess group."""
    if not command or timeout_s <= 0 or maximum_output < 0:
        raise OperationError("invalid_process_limit")
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            pass_fds=pass_fds,
            env=env,
            stdin=input_fd if input_fd is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + timeout_s
        count = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if cancelled is not None and cancelled():
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        pass
                    raise OperationError("service_stopping")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OperationError("process_timeout")
                for key, _ in selector.select(min(0.1, remaining)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > maximum_output:
                        raise OperationError("process_output_limit")
                    if output_fd is not None:
                        view = memoryview(chunk)
                        while view:
                            written = os.write(output_fd, view)
                            if written <= 0:
                                raise OperationError("file_unavailable")
                            view = view[written:]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationError("process_timeout")
            if process.wait(timeout=remaining) != 0:
                raise OperationError("process_failed")
    except (OSError, subprocess.TimeoutExpired):
        raise OperationError("process_failed") from None
    finally:
        if process is not None:
            # A child may outlive the command leader even after a successful exit.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
