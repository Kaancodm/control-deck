"""Explicit lane supervisor; service shutdown never impersonates owner cancellation."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
from collections.abc import Mapping

from app.core.schemas import ATTEMPT_TIMEOUT_MAX_S, Worker
from app.operations.errors import OperationError
from app.operations.process import run_command
from app.workers.live_config import LiveConfigurationError, load_live_config

_ENVIRONMENT = {
    "DECK_DATABASE_URL",
    "DECK_RUNS_DIR",
    "DECK_INBOX_DIR",
    "DECK_BLOCKLIST_PATH",
    "DECK_ROUTER_FILE",
    "DECK_LIVE_ENABLED",
    "DECK_OLLAMA_URL",
}


def runner_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Forward only host-runner configuration, excluding owner and lane credentials."""
    return {"PATH": os.defpath, **{key: source[key] for key in _ENVIRONMENT if key in source}}


def run_loop(
    worker: Worker,
    *,
    environment: dict[str, str],
    stop: threading.Event,
    iterations: int = 0,
    poll_seconds: float = 1,
    attempt_timeout_s: float = ATTEMPT_TIMEOUT_MAX_S,
    live: bool = False,
    command: tuple[str, ...] | None = None,
) -> int:
    """Run separate host attempts, preserving durable queue retries after a forced stop."""
    if (
        not isinstance(worker, Worker)
        or type(iterations) is not int
        or not 0 <= iterations <= 10000
        or not math.isfinite(poll_seconds)
        or not 0.1 <= poll_seconds <= 60
        or not math.isfinite(attempt_timeout_s)
        or not 0.1 <= attempt_timeout_s <= 7200
    ):
        raise OperationError("invalid_runner_limits")
    if live:
        load_live_config(worker, environment)
    selected = command or (
        sys.executable,
        "-m",
        "app.workers.runner_main",
        "--worker",
        worker.value,
        "--live" if live else "--simulate",
        "--once",
        "--executor",
        "ollama-live" if live else worker.value + "-fake-cli",
    )
    completed = 0
    while not stop.is_set() and (iterations == 0 or completed < iterations):
        delay = poll_seconds
        try:
            run_command(
                selected,
                env=environment,
                timeout_s=attempt_timeout_s,
                maximum_output=65536,
                cancelled=stop.is_set,
            )
        except OperationError as error:
            if error.code == "service_stopping":
                break
            if live:
                # Unavailable providers are normal runner outcomes. A failed host
                # process must reach systemd's supervisor restart policy instead.
                raise
            delay = max(5, poll_seconds)
        completed += 1
        if iterations and completed >= iterations:
            break
        stop.wait(delay)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded, explicitly enabled lane supervisor"
    )
    parser.add_argument("--worker", required=True, choices=[lane.value for lane in Worker])
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--iterations", type=int, default=0)
    arguments = parser.parse_args()
    if not arguments.simulate:
        try:
            load_live_config(Worker(arguments.worker))
        except LiveConfigurationError as error:
            print(json.dumps({"status": "unavailable", "reason": error.code}))
            return 78
    stop = threading.Event()

    def terminate(_number: int, _frame: object) -> None:
        stop.set()

    previous = {
        number: signal.signal(number, terminate) for number in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        completed = run_loop(
            Worker(arguments.worker),
            environment=runner_environment(os.environ),
            stop=stop,
            iterations=arguments.iterations,
            live=not arguments.simulate,
        )
        print(json.dumps({"status": "stopped", "iterations": completed}))
        return 0
    except OperationError as error:
        print(json.dumps({"status": "failed", "reason": error.code}))
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
