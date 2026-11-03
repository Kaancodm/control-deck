"""Explicit Codex Fake-CLI selection cannot enable a real provider or wrong lane."""

import json
import sys

import pytest

from app.routing.config import RouterConfig
from app.workers import runner_main
from app.workers.lanes.codex import CodexExecutor
from app.workers.runner import RunOutcome


@pytest.mark.parametrize("worker", ["ollama", "claude"])
def test_codex_selector_rejects_other_lanes_before_database_access(monkeypatch, worker):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--worker", worker, "--simulate", "--once", "--executor", "codex-fake-cli"],
    )

    def forbidden():
        raise AssertionError("Wrong lane must not access the database")

    monkeypatch.setattr(runner_main, "get_session_factory", forbidden)
    with pytest.raises(SystemExit) as raised:
        runner_main.main()
    assert raised.value.code == 2


@pytest.mark.parametrize("action,code", [("idle", 0), ("error", 1), ("unavailable", 1)])
def test_codex_selector_constructs_only_versioned_simulation(monkeypatch, capsys, action, code):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--worker", "codex", "--simulate", "--once", "--executor", "codex-fake-cli"],
    )
    config = RouterConfig.model_validate(
        {"lanes": {"codex": {"models": {"default": "fixture-a", "summarize": "fixture-b"}}}}
    )
    monkeypatch.setattr(runner_main, "get_router_config", lambda: config)
    factory = object()
    monkeypatch.setattr(runner_main, "get_session_factory", lambda: factory)
    created = []

    class FakeRunner:
        def __init__(self, session_factory, *, runs_root, executor):
            assert session_factory is factory
            assert isinstance(executor, CodexExecutor)
            assert executor.profile == "simulation"
            assert executor.simulation.models == ("fixture-a", "fixture-b")
            assert executor.simulation.cleanroom.cli_version == "0.166.0"
            created.append(executor)

        def run_once(self):
            return RunOutcome(action)

    monkeypatch.setattr(runner_main, "CodexLaneRunner", FakeRunner)
    assert runner_main.main() == code
    assert len(created) == 1
    assert json.loads(capsys.readouterr().out)["action"] == action
