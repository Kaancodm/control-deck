"""The command can explicitly select the bundled fake CLI, never a live provider."""

from __future__ import annotations

import json
import sys

import pytest

from app.routing.config import RouterConfig
from app.workers import runner_main
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.runner import RunOutcome


@pytest.mark.parametrize(
    "arguments",
    [
        ["--worker", "ollama", "--once", "--executor", "ollama-fake-cli"],
        ["--worker", "ollama", "--simulate", "--once", "--executor", "ollama"],
        ["--worker", "claude", "--simulate", "--once", "--executor", "ollama-fake-cli"],
    ],
)
def test_invalid_execution_modes_fail_before_database_access(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["runner", *arguments])

    def forbidden_database():
        raise AssertionError("Invalid command must not access the database")

    monkeypatch.setattr(runner_main, "get_session_factory", forbidden_database)
    with pytest.raises(SystemExit) as raised:
        runner_main.main()
    assert raised.value.code == 2


@pytest.mark.parametrize("action,exit_code", [("idle", 0), ("unavailable", 1), ("error", 1)])
def test_fake_cli_selection_uses_only_configured_simulation(monkeypatch, capsys, action, exit_code):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--worker", "ollama", "--simulate", "--once", "--executor", "ollama-fake-cli"],
    )
    config = RouterConfig.model_validate(
        {"lanes": {"ollama": {"models": {"default": "fixture-a", "summarize": "fixture-b"}}}}
    )
    monkeypatch.setattr(runner_main, "get_router_config", lambda: config)
    factory = object()
    monkeypatch.setattr(runner_main, "get_session_factory", lambda: factory)
    constructed = []

    class FakeLaneRunner:
        def __init__(self, session_factory, *, runs_root, executor):
            assert session_factory is factory
            assert isinstance(executor, OllamaExecutor)
            assert executor.profile == "simulation"
            assert executor.simulation is not None
            assert executor.simulation.models == ("fixture-a", "fixture-b")
            constructed.append(executor)

        def run_once(self):
            return RunOutcome(
                action, error_code="model_not_available" if action != "idle" else None
            )

    monkeypatch.setattr(runner_main, "OllamaLaneRunner", FakeLaneRunner)
    assert runner_main.main() == exit_code
    assert len(constructed) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["action"] == action
    assert "fixture-a" not in json.dumps(report)
