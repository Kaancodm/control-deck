"""Reference execution shares routing without reading machine configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.routing.config import RouterConfig

from app.core.schemas import ResultStatus, TaskV1, Worker
from app.reference.runner import FakeExecutor


def _task(**updates):
    payload = json.loads((Path(__file__).parent / "examples/task_valid_01.json").read_text())
    payload.update(updates)
    return TaskV1.model_validate(payload)


def test_fake_local_only_overrides_cloud_chain_without_mutating_task():
    task = _task(fallback_chain=["claude", "codex"])
    original = task.model_dump(mode="json")
    result = FakeExecutor().execute(task)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.worker == Worker.OLLAMA
    assert task.model_dump(mode="json") == original


@pytest.mark.parametrize(
    "policy",
    [
        {"allowed_workers": []},
        {"allowed_workers": ["claude"]},
        {"forbidden_workers": ["ollama"]},
    ],
)
def test_fake_local_override_cannot_bypass_policy(policy, monkeypatch):
    task = _task(fallback_chain=["claude"], policy=policy)
    executor = FakeExecutor()

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("Blocked routing must not execute")

    monkeypatch.setattr(executor, "_execute_payload", forbidden_execution)
    result = executor.execute(task)
    assert result.status == ResultStatus.BLOCKED
    assert result.worker == Worker.OLLAMA
    assert not executor._maps


def test_fake_cloud_chain_uses_first_policy_permitted_lane():
    task = _task(
        data_class="cloud_ok",
        fallback_chain=["claude", "codex", "ollama"],
        policy={"allowed_workers": ["codex", "ollama"]},
    )
    result = FakeExecutor().execute(task)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.worker == Worker.CODEX


def test_fake_reference_does_not_read_environment_router_file(monkeypatch):
    monkeypatch.setenv("DECK_ROUTER_FILE", "/unavailable/reference-config.yaml")
    result = FakeExecutor().execute(_task())
    assert result.status == ResultStatus.SUCCEEDED
    assert result.worker == Worker.OLLAMA


def test_fake_obeys_explicit_budget_configuration_without_execution(monkeypatch):
    config = RouterConfig.model_validate(
        {"lanes": {worker: {"daily_attempt_limit": 0} for worker in ("ollama", "claude", "codex")}}
    )
    executor = FakeExecutor(router_config=config)

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("Deferred routing must not execute")

    monkeypatch.setattr(executor, "_execute_payload", forbidden_execution)
    result = executor.execute(_task())
    assert result.status == ResultStatus.BLOCKED
    assert not executor._maps
