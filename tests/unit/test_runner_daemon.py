"""Supervisor lifecycle does not silently activate simulations or mutate task control."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.core.schemas import ATTEMPT_TIMEOUT_MAX_S, Worker
from app.operations.errors import OperationError, ProcessExitError
from app.workers import daemon

LIVE_ENVIRONMENT = {"DECK_LIVE_ENABLED": "1", "DECK_OLLAMA_URL": "http://127.0.0.1:11434"}


def attempt_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(item.getMessage()) for item in caplog.records if item.name == daemon._log.name
    ]


def test_live_service_profile_stays_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECK_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(sys, "argv", ["daemon", "--worker", "ollama"])
    monkeypatch.setattr(daemon, "run_loop", lambda *args, **kwargs: pytest.fail("No runner"))
    assert daemon.main() == 78


@pytest.mark.parametrize(
    "flag,worker,url,reason",
    [
        ("0", "ollama", "", "live_disabled"),
        ("TRUE", "ollama", "", "live_disabled"),
        ("1", "claude", "", "live_lane_not_supported"),
        ("1", "codex", "", "live_lane_not_supported"),
        ("1", "ollama", "", "live_url_invalid"),
        ("true", "ollama", "http://localhost:11434", "live_url_invalid"),
    ],
)
def test_configuration_refusal_never_spawns(monkeypatch, capsys, flag, worker, url, reason):
    monkeypatch.setenv("DECK_LIVE_ENABLED", flag)
    monkeypatch.setenv("DECK_OLLAMA_URL", url)
    monkeypatch.setattr(sys, "argv", ["daemon", "--worker", worker])
    monkeypatch.setattr(daemon, "run_loop", lambda *args, **kwargs: pytest.fail("No runner"))
    assert daemon.main() == 78
    assert json.loads(capsys.readouterr().out) == {"status": "unavailable", "reason": reason}


def test_bounded_supervisor_uses_one_explicit_lane_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    monkeypatch.setattr(daemon, "run_command", lambda command, **kwargs: commands.append(command))
    count = daemon.run_loop(
        Worker.OLLAMA, environment={}, stop=threading.Event(), iterations=2, poll_seconds=0.1
    )
    assert count == 2 and len(commands) == 2
    assert all(command[-1] == "ollama-fake-cli" and "--simulate" in command for command in commands)


def test_live_supervisor_uses_live_executor_exclusively(monkeypatch):
    commands = []
    monkeypatch.setattr(daemon, "run_command", lambda command, **kwargs: commands.append(command))
    environment = {"DECK_LIVE_ENABLED": "1", "DECK_OLLAMA_URL": "http://127.0.0.1:11434"}
    assert (
        daemon.run_loop(
            Worker.OLLAMA, environment=environment, stop=threading.Event(), iterations=1, live=True
        )
        == 1
    )
    assert commands[0][-1] == "ollama-live"
    assert "--live" in commands[0] and "--simulate" not in commands[0]


@pytest.mark.parametrize("simulate", [False, True])
def test_main_selects_profile_and_simulation_ignores_live_configuration(
    monkeypatch, capsys, simulate
):
    monkeypatch.setenv("DECK_LIVE_ENABLED", "1")
    monkeypatch.setenv("DECK_OLLAMA_URL", "invalid" if simulate else "http://127.0.0.1:11434")
    monkeypatch.setattr(
        sys, "argv", ["daemon", "--worker", "ollama"] + (["--simulate"] if simulate else [])
    )
    seen = []
    monkeypatch.setattr(daemon, "run_loop", lambda *args, **kwargs: seen.append(kwargs) or 0)
    assert daemon.main() == 0
    assert seen[0]["live"] is not simulate
    assert json.loads(capsys.readouterr().out)["status"] == "stopped"


@pytest.mark.parametrize("reason", ["process_failed", "process_timeout", "process_output_limit"])
def test_live_host_process_failure_exits_one_for_supervisor_restart(
    monkeypatch, capsys, caplog, reason
):
    calls = []
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(sys, "argv", ["daemon", "--worker", "ollama"])

    def child(command, **kwargs):
        calls.append(command)
        raise OperationError(reason)

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.main() == 1
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "reason": reason}
    assert [(item["reason"], item["during_stop"]) for item in attempt_records(caplog)] == [
        (reason, False)
    ]


def test_stop_cancels_child_without_a_database_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()

    def child(command, **kwargs):
        stop.set()
        assert kwargs["cancelled"]()
        raise OperationError("service_stopping")

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.run_loop(Worker.CODEX, environment={}, stop=stop, iterations=2) == 0


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        OperationError("process_failed"),
        OperationError("process_timeout"),
        OperationError("process_output_limit"),
        ProcessExitError(1),
        ProcessExitError(-signal.SIGKILL),
    ],
    ids=["os-error", "timeout", "output-limit", "exit-1", "sigkill"],
)
def test_finished_attempt_during_a_stop_is_recorded_then_ends(monkeypatch, caplog, live, failure):
    stop = threading.Event()
    calls = []

    def child(command, **kwargs):
        calls.append(command)
        stop.set()
        raise failure

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.run_loop(Worker.OLLAMA, environment=LIVE_ENVIRONMENT, stop=stop, live=live) == 1
    assert len(calls) == 1
    assert attempt_records(caplog) == [
        {
            "event": "runner_attempt_failed",
            "worker": "ollama",
            "reason": failure.code,
            "during_stop": True,
        }
    ]


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_child_killed_by_the_stop_signal_is_a_clean_stop(monkeypatch, caplog, live, number):
    stop = threading.Event()

    def child(command, **kwargs):
        stop.set()
        raise ProcessExitError(-number)

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.run_loop(Worker.OLLAMA, environment=LIVE_ENVIRONMENT, stop=stop, live=live) == 0
    assert attempt_records(caplog) == []


def test_signal_death_without_a_stop_stays_a_failure(monkeypatch, caplog):
    def child(command, **kwargs):
        raise ProcessExitError(-signal.SIGTERM)

    monkeypatch.setattr(daemon, "run_command", child)
    with pytest.raises(ProcessExitError):
        daemon.run_loop(
            Worker.OLLAMA, environment=LIVE_ENVIRONMENT, stop=threading.Event(), live=True
        )
    assert [record["during_stop"] for record in attempt_records(caplog)] == [False]


@pytest.mark.parametrize("reason", ["process_timeout", "process_output_limit"])
def test_shutdown_exits_zero_after_recording_a_finished_attempt(
    monkeypatch, capsys, caplog, reason
):
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(sys, "argv", ["daemon", "--worker", "ollama"])

    def child(command, **kwargs):
        signal.raise_signal(signal.SIGTERM)
        raise OperationError(reason)

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.main() == 0
    assert json.loads(capsys.readouterr().out) == {"status": "stopped", "iterations": 1}
    assert [(item["reason"], item["during_stop"]) for item in attempt_records(caplog)] == [
        (reason, True)
    ]


_SIGNAL_HARNESS = """
import sys
from app.workers import daemon

pid_file = sys.argv[1]
child = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"
run_loop = daemon.run_loop
daemon.run_loop = lambda worker, **kwargs: run_loop(
    worker, command=(sys.executable, "-c", child, pid_file), **kwargs
)
sys.argv = ["daemon", "--worker", "ollama"]
raise SystemExit(daemon.main())
"""


def _child_pid(path: Path, supervisor: subprocess.Popen[bytes]) -> int:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and supervisor.poll() is None:
        if path.exists() and (text := path.read_text()):
            return int(text)
        time.sleep(0.01)
    raise AssertionError("Attempt process did not start")


@pytest.mark.parametrize(
    ("number", "signal_child"),
    [
        # KillMode=mixed: only the supervisor is signalled.
        (signal.SIGTERM, False),
        # KillMode=control-group: systemd signals the main process, then the rest.
        (signal.SIGTERM, True),
        (signal.SIGINT, False),
    ],
)
def test_service_signal_exits_zero_during_an_attempt(tmp_path, number, signal_child):
    pid_file = tmp_path / "attempt.pid"
    environment = {
        "PATH": os.defpath,
        "DECK_LIVE_ENABLED": "1",
        "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
    }
    supervisor = subprocess.Popen(
        [sys.executable, "-c", _SIGNAL_HARNESS, str(pid_file)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        child = _child_pid(pid_file, supervisor)
        supervisor.send_signal(number)
        if signal_child:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, number)
        stdout, stderr = supervisor.communicate(timeout=20)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait()
    assert supervisor.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {"status": "stopped", "iterations": 0}
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_supervisor_default_matches_the_attempt_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, float] = {}

    def child(command: object, **kwargs: object) -> None:
        timeout = kwargs["timeout_s"]
        assert isinstance(timeout, int | float)
        seen["timeout_s"] = timeout
        raise OperationError("service_stopping")

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.run_loop(Worker.OLLAMA, environment={}, stop=threading.Event(), iterations=1) == 0
    assert seen["timeout_s"] == ATTEMPT_TIMEOUT_MAX_S == 7200


def test_supervisor_passes_only_host_configuration() -> None:
    environment = daemon.runner_environment(
        {
            "DECK_DATABASE_URL": "test-database",
            "DECK_ROUTER_FILE": "fixture-config",
            "DECK_LIVE_ENABLED": "1",
            "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
            "OLLAMA_HOST": "untrusted",
            "HTTP_PROXY": "untrusted",
            "DECK_OWNER_TOKEN": "fake-owner-value",
            "DECK_WORKER_TOKEN_CLAUDE": "fake-lane-value",
            "ANTHROPIC_API_KEY": "fake-key-value",
            "HOME": "/home/other-account",
            "CLAUDE_CONFIG_DIR": "/home/other-account/.claude",
            "CODEX_HOME": "/home/other-account/.codex",
            "SUDO_USER": "other-account",
            "SSH_AUTH_SOCK": "/tmp/ssh-agent",
            "PATH": "untrusted",
        }
    )
    assert environment == {
        "PATH": os.defpath,
        "DECK_DATABASE_URL": "test-database",
        "DECK_ROUTER_FILE": "fixture-config",
        "DECK_LIVE_ENABLED": "1",
        "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"iterations": -1},
        {"iterations": True},
        {"poll_seconds": 0},
        {"poll_seconds": float("nan")},
        {"attempt_timeout_s": 9000},
    ],
)
def test_invalid_supervisor_limits_are_rejected(changes: dict) -> None:
    with pytest.raises(OperationError):
        daemon.run_loop(Worker.OLLAMA, environment={}, stop=threading.Event(), **changes)
