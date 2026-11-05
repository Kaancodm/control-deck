"""Probe Ollama inventory before admitting a simulation or live host attempt."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.schemas import Worker
from app.db.settings import read_settings
from app.routing.config import get_router_config
from app.workers.lane_status import LaneHealthState, update_lane_status
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.runner import HostRunner, RunOutcome


class OllamaLaneRunner:
    """Keep availability I/O outside transactions and unavailable lanes unclaimed."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        runs_root: Path,
        executor: OllamaExecutor,
        lease_ttl: timedelta = timedelta(seconds=90),
        heartbeat_interval_s: float | None = None,
    ) -> None:
        if heartbeat_interval_s is None:
            heartbeat_interval_s = 0.5 if executor.profile == "live" else 5.0
        self.session_factory = session_factory
        self.executor = executor
        self.runner = HostRunner(
            session_factory,
            runs_root=runs_root,
            lease_ttl=lease_ttl,
            heartbeat_interval_s=heartbeat_interval_s,
        )

    def _record(
        self, state: LaneHealthState, reason: str, *, probe_generation: int | None = None
    ) -> int:
        with self.session_factory() as session:
            row = update_lane_status(
                session,
                lane=Worker.OLLAMA,
                status=state,
                reason=reason,
                probe_generation=probe_generation,
            )
            generation = row.probe_generation
            session.commit()
            return generation

    def run_once(self) -> RunOutcome:
        """Probe and run at most one attempt; no provider call holds a DB transaction."""
        try:
            configuration = get_router_config()
            with self.session_factory() as session:
                controls = read_settings(session)
            lane = configuration.lanes[Worker.OLLAMA]
            if controls.stop_requested or controls.paused or controls.lane_paused[Worker.OLLAMA]:
                self._record(LaneHealthState.PAUSED, "runner_paused")
                return RunOutcome("idle", error_code="lane_paused")
            if not lane.enabled:
                self._record(LaneHealthState.PAUSED, "lane_disabled")
                return RunOutcome("idle", error_code="lane_disabled")
            models = tuple(sorted(set(lane.models.values())))
            self.executor.num_ctx = lane.num_ctx
            generation = self._record(LaneHealthState.UNAVAILABLE, "probe_pending")
            availability = self.executor.availability(models)
            state = (
                LaneHealthState.OK if availability.status == "ok" else LaneHealthState.UNAVAILABLE
            )
            self._record(state, availability.reason, probe_generation=generation)
            if availability.status != "ok":
                return RunOutcome("unavailable", error_code=availability.reason)
            return self.runner.run_once(
                Worker.OLLAMA,
                self.executor,
                available_models=frozenset(availability.models),
            )
        except Exception:
            # Configuration, SQL or process errors may embed raw values and paths.
            # A failed health transaction must never proceed to claim work.
            return RunOutcome("error", error_code="lane_preflight_failed")
