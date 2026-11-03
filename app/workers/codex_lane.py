"""Host admission and health for the explicitly simulated Codex lane."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.schemas import Worker
from app.db.models import Result
from app.db.settings import read_settings
from app.routing.config import get_router_config
from app.workers.lane_status import LaneHealthState, get_lane_status, update_lane_status
from app.workers.lanes.codex import CodexExecutor
from app.workers.runner import HostRunner, RunOutcome


class CodexLaneRunner:
    """Keep simulation observations distinct from verified live availability."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        runs_root: Path,
        executor: CodexExecutor,
        lease_ttl: timedelta = timedelta(seconds=90),
        heartbeat_interval_s: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.executor = executor
        self.runner = HostRunner(
            session_factory,
            runs_root=runs_root,
            lease_ttl=lease_ttl,
            heartbeat_interval_s=heartbeat_interval_s,
        )

    def _record(self, status: LaneHealthState, reason: str) -> None:
        with self.session_factory() as session:
            current = get_lane_status(session, lane=Worker.CODEX)
            if (
                status == LaneHealthState.PAUSED
                and reason == "runner_paused"
                and current is not None
                and current.status == LaneHealthState.AUTH_REQUIRED.value
            ):
                # Polling a core-paused lane must preserve its actionable auth reason.
                return
            update_lane_status(session, lane=Worker.CODEX, status=status, reason=reason)
            session.commit()

    def run_once(self) -> RunOutcome:
        try:
            configuration = get_router_config()
            with self.session_factory() as session:
                controls = read_settings(session)
            lane = configuration.lanes[Worker.CODEX]
            if controls.stop_requested or controls.paused or controls.lane_paused[Worker.CODEX]:
                self._record(LaneHealthState.PAUSED, "runner_paused")
                return RunOutcome("idle", error_code="lane_paused")
            if not lane.enabled:
                self._record(LaneHealthState.PAUSED, "lane_disabled")
                return RunOutcome("idle", error_code="lane_disabled")
            available = self.executor.availability(tuple(sorted(set(lane.models.values()))))
            self._record(
                LaneHealthState.OK if available.status == "ok" else LaneHealthState.UNAVAILABLE,
                available.reason,
            )
            if available.status != "ok":
                return RunOutcome("unavailable", error_code=available.reason)
            outcome = self.runner.run_once(
                Worker.CODEX, self.executor, available_models=frozenset(available.models)
            )
            if outcome.action == "completed":
                with self.session_factory() as session:
                    payload = session.scalar(
                        select(Result.payload).where(
                            Result.task_id == outcome.task_id, Result.attempt == outcome.attempt
                        )
                    )
                error = payload.get("error") if payload else None
                code = error.get("code") if isinstance(error, dict) else None
                if code == "auth_required":
                    self._record(LaneHealthState.AUTH_REQUIRED, "simulation_auth_required")
                elif code == "policy_blocked":
                    self._record(LaneHealthState.PAUSED, "simulation_policy_blocked")
                elif code == "worker_unavailable":
                    self._record(LaneHealthState.UNAVAILABLE, "simulation_worker_unavailable")
            return outcome
        except Exception:
            return RunOutcome("error", error_code="lane_preflight_failed")
