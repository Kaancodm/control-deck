"""Claude CLI selection remains explicit and cannot activate a live provider."""

from __future__ import annotations

import json
import sys

import pytest

from app.core.schemas import Worker
from app.routing.config import RouterConfig
from app.workers import runner_main
from app.workers.lanes.claude import ClaudeExecutor
from app.workers.runner import RunOutcome


@pytest.mark.parametrize(
    "arguments",
    [
        ["--worker", "claude", "--once", "--executor", "claude-fake-cli"],
        ["--worker", "ollama", "--simulate", "--once", "--executor", "claude-fake-cli"],
        ["--worker", "codex", "--simulate", "--once", "--executor", "claude-fake-cli"],
        ["--worker", "claude", "--simulate", "--once", "--executor", "claude"],
    ],
)
def test_invalid_claude_mode_is_rejected_before_database_access(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["runner", *arguments])

    def forbidden_database():
        pytest.fail("Invalid command must not access the database")

    monkeypatch.setattr(runner_main, "get_session_factory", forbidden_database)
    with pytest.raises(SystemExit) as raised:
        runner_main.main()
    assert raised.value.code == 2


@pytest.mark.parametrize(
    "action,exit_code", [("idle", 0), ("completed", 0), ("unavailable", 1), ("error", 1)]
)
def test_claude_selector_uses_only_configured_fake_profile(monkeypatch, capsys, action, exit_code):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--worker", "claude", "--simulate", "--once", "--executor", "claude-fake-cli"],
    )
    configuration = RouterConfig.model_validate(
        {"lanes": {"claude": {"models": {"default": "fixture-a", "summarize": "fixture-b"}}}}
    )
    monkeypatch.setattr(runner_main, "get_router_config", lambda: configuration)
    factory = object()
    monkeypatch.setattr(runner_main, "get_session_factory", lambda: factory)
    selected = []

    class FakeRunner:
        def __init__(self, session_factory, *, runs_root, executor):
            assert session_factory is factory
            assert isinstance(executor, ClaudeExecutor)
            assert executor.lane == Worker.CLAUDE
            assert executor.profile == "simulation"
            assert executor.simulation.models == ("fixture-a", "fixture-b")
            selected.append(executor)

        def run_once(self):
            return RunOutcome(action)

    monkeypatch.setattr(runner_main, "ClaudeLaneRunner", FakeRunner)
    assert runner_main.main() == exit_code
    assert len(selected) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["action"] == action
    assert "fixture-a" not in json.dumps(output)
