"""Describe locally observed health without pretending to query provider quotas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.schemas import ErrorCode, ResultStatus, Worker
from app.workers.contracts import ExecutionProfile

LIVE_OBSERVATION_TTL = timedelta(seconds=30)

_KNOWN_REASONS = frozenset(code.value for code in ErrorCode) | {
    "simulation_ready",
    "simulation_auth_required",
    "simulation_policy_blocked",
    "simulation_worker_unavailable",
    "live_profile_unavailable",
    "live_ready",
    "live_disabled",
    "live_url_invalid",
    "live_lane_not_supported",
    "model_capability_unsupported",
    "model_unsupported",
    "version_unsupported",
    "runner_paused",
    "lane_disabled",
    "model_not_configured",
    "model_not_available",
    "model_unavailable",
    "cli_unavailable",
    "rootless_required",
    "cli_version_unsupported",
    "context_limit_exceeded",
    "inventory_invalid",
    "inventory_unavailable",
    "inventory_timeout",
    "inventory_cancelled",
    "inventory_internal",
    "probe_pending",
    "attempt_recorded",
}


@dataclass(frozen=True)
class LaneObservation:
    status: str
    reason: str
    observed_at: datetime
    last_observation_success_at: datetime | None


@dataclass(frozen=True)
class AttemptHealth:
    simulated_attempts: int = 0
    remote_attempts: int = 0
    unknown_attempts: int = 0
    completed_attempts: int = 0
    consecutive_failures: int = 0
    last_error_code: ErrorCode | None = None
    last_error_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_attempt_success_at: datetime | None = None


def completed_attempt(
    previous: AttemptHealth,
    *,
    status: ResultStatus,
    error_code: ErrorCode | None,
    recorded_at: datetime,
    execution_profile: ExecutionProfile = "simulation",
) -> AttemptHealth:
    """Apply one finally accepted result; callers enforce uniqueness and fencing."""
    if recorded_at.tzinfo is None or not isinstance(status, ResultStatus):
        raise ValueError("Invalid completed attempt metadata")
    if execution_profile not in {"simulation", "live", "remote", "unknown"}:
        raise ValueError("Invalid execution profile")
    live = execution_profile == "live"
    failed = status in (ResultStatus.FAILED, ResultStatus.BLOCKED)
    succeeded = live and status == ResultStatus.SUCCEEDED
    return AttemptHealth(
        simulated_attempts=previous.simulated_attempts + (execution_profile == "simulation"),
        remote_attempts=previous.remote_attempts + (execution_profile == "remote"),
        unknown_attempts=previous.unknown_attempts + (execution_profile == "unknown"),
        completed_attempts=previous.completed_attempts + live,
        consecutive_failures=(
            previous.consecutive_failures + 1
            if failed
            else 0
            if succeeded
            else previous.consecutive_failures
        ),
        last_error_code=error_code if failed else previous.last_error_code,
        last_error_at=recorded_at if failed else previous.last_error_at,
        last_attempt_at=recorded_at if live else previous.last_attempt_at,
        last_attempt_success_at=recorded_at if succeeded else previous.last_attempt_success_at,
    )


class LaneHealthView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lane: Worker
    enabled: bool
    paused: bool
    status: Literal["unknown", "ok", "unavailable", "auth_required", "paused"]
    reason: str
    execution_mode: Literal["unknown", "simulation", "live", "unavailable"]
    live_available: bool = False
    provider_quota: Literal["unknown"] = "unknown"
    daily_attempt_limit: int | None
    claims_today: int
    configured_attempts_remaining: int | None
    observed_at: datetime | None
    last_observation_success_at: datetime | None
    simulated_attempts: int
    remote_attempts: int
    unknown_attempts: int
    completed_attempts: int
    consecutive_failures: int
    last_error_code: ErrorCode | None
    last_error_at: datetime | None
    last_attempt_at: datetime | None
    last_attempt_success_at: datetime | None
    warnings: list[Literal["auth_required", "no_recent_success"]]


def lane_health_view(
    *,
    lane: Worker,
    enabled: bool,
    paused: bool,
    daily_attempt_limit: int | None,
    claims_today: int,
    observation: LaneObservation | None,
    attempts: AttemptHealth,
    first_claim_at: datetime | None,
    now: datetime,
) -> LaneHealthView:
    """Separate positive preflight observations from completed attempt outcomes."""
    if claims_today < 0 or (daily_attempt_limit is not None and daily_attempt_limit < 0):
        raise ValueError("Invalid local claim counters")
    reason = (
        observation.reason if observation and observation.reason in _KNOWN_REASONS else "unknown"
    )
    status = (
        observation.status
        if observation and observation.status in {"ok", "unavailable", "auth_required", "paused"}
        else "unknown"
    )
    mode = (
        "live"
        if reason == "live_ready" and status == "ok"
        else "simulation"
        if reason.startswith("simulation_")
        else "unavailable"
        if reason
        in {
            "live_profile_unavailable",
            "live_disabled",
            "live_url_invalid",
            "live_lane_not_supported",
        }
        else "unknown"
    )
    warnings: list[Literal["auth_required", "no_recent_success"]] = []
    if status == "auth_required":
        warnings.append("auth_required")
    last_success_basis = attempts.last_attempt_success_at or first_claim_at
    if last_success_basis is not None and now - last_success_basis >= timedelta(hours=24):
        warnings.append("no_recent_success")
    return LaneHealthView.model_validate(
        {
            "lane": lane,
            "enabled": enabled,
            "paused": paused,
            "status": status,
            "reason": reason,
            "execution_mode": mode,
            "live_available": (
                mode == "live"
                and enabled
                and not paused
                and observation is not None
                and timedelta(0) <= now - observation.observed_at < LIVE_OBSERVATION_TTL
            ),
            "daily_attempt_limit": daily_attempt_limit,
            "claims_today": claims_today,
            "configured_attempts_remaining": (
                max(0, daily_attempt_limit - claims_today)
                if daily_attempt_limit is not None
                else None
            ),
            "observed_at": observation.observed_at if observation else None,
            "last_observation_success_at": observation.last_observation_success_at
            if observation
            else None,
            "simulated_attempts": attempts.simulated_attempts,
            "remote_attempts": attempts.remote_attempts,
            "unknown_attempts": attempts.unknown_attempts,
            "completed_attempts": attempts.completed_attempts,
            "consecutive_failures": attempts.consecutive_failures,
            "last_error_code": attempts.last_error_code,
            "last_error_at": attempts.last_error_at,
            "last_attempt_at": attempts.last_attempt_at,
            "last_attempt_success_at": attempts.last_attempt_success_at,
            "warnings": warnings,
        }
    )
