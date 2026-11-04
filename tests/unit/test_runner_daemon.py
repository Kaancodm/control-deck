"""Supervisor lifecycle does not silently activate simulations or mutate task control."""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

from app.core.schemas import Worker
from app.operations.errors import OperationError
from app.workers import daemon


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
        ("1", "ollama", "http://127.0.0.1:11434", "live_profile_unavailable"),
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


def test_stop_cancels_child_without_a_database_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()

    def child(command, **kwargs):
        stop.set()
        assert kwargs["cancelled"]()
        raise OperationError("service_stopping")

    monkeypatch.setattr(daemon, "run_command", child)
    assert daemon.run_loop(Worker.CODEX, environment={}, stop=stop, iterations=2) == 0


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
