"""Simulated adapters must declare a concrete lane before they can be used."""

from __future__ import annotations

import pytest

from app.core.schemas import Worker
from app.workers.runner_main import SimulationExecutor


@pytest.mark.parametrize("lane", list(Worker))
def test_simulation_executor_declares_its_configured_lane(lane: Worker) -> None:
    assert SimulationExecutor(lane).lane is lane


@pytest.mark.parametrize("lane", [None, "ollama", "claude", 0])
def test_simulation_executor_rejects_untyped_lane(lane: object) -> None:
    with pytest.raises(ValueError, match="Executor lane must be a Worker"):
        SimulationExecutor(lane)
