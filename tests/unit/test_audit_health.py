"""Local observations never manufacture provider readiness or quota information."""

from datetime import UTC, datetime, timedelta

import pytest

from app.audit.health import AttemptHealth, LaneObservation, lane_health_view
from app.core.schemas import ErrorCode, Worker

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def view(**changes):
    parameters = {
        "lane": Worker.OLLAMA,
        "enabled": True,
        "paused": False,
        "daily_attempt_limit": None,
        "claims_today": 0,
        "observation": None,
        "attempts": AttemptHealth(),
        "first_claim_at": None,
        "now": NOW,
        **changes,
    }
    return lane_health_view(**parameters)


def test_unobserved_lane_is_unknown_without_invented_success():
    result = view()
    assert result.status == result.execution_mode == result.provider_quota == "unknown"
    assert result.live_available is False
    assert result.last_attempt_success_at is result.last_observation_success_at is None
    assert result.configured_attempts_remaining is None
    assert result.warnings == []


def test_simulation_success_does_not_mean_live_provider_success():
    result = view(observation=LaneObservation("ok", "simulation_ready", NOW, NOW))
    assert result.status == "ok" and result.execution_mode == "simulation"
    assert result.live_available is False and result.provider_quota == "unknown"
    assert result.completed_attempts == 0
    assert result.last_attempt_success_at is None


def test_local_budget_remaining_is_explicitly_separate_from_provider_quota():
    result = view(daily_attempt_limit=10, claims_today=3)
    assert result.configured_attempts_remaining == 7
    assert result.daily_attempt_limit == 10 and result.claims_today == 3
    assert result.provider_quota == "unknown"
    assert view(daily_attempt_limit=1, claims_today=3).configured_attempts_remaining == 0


@pytest.mark.parametrize("reason", ["auth_required", "simulation_auth_required"])
def test_auth_failure_is_visible_without_any_completed_attempt(reason):
    result = view(observation=LaneObservation("auth_required", reason, NOW, None), paused=True)
    assert result.warnings == ["auth_required"]
    assert result.paused is True
    assert result.completed_attempts == 0


def test_recent_positive_preflight_does_not_hide_stale_attempts():
    result = view(
        first_claim_at=NOW - timedelta(days=2),
        observation=LaneObservation("ok", "simulation_ready", NOW, NOW),
    )
    assert result.warnings == ["no_recent_success"]


def test_actual_attempt_recovery_resets_stale_warning_basis():
    result = view(
        first_claim_at=NOW - timedelta(days=2),
        attempts=AttemptHealth(
            completed_attempts=4,
            consecutive_failures=0,
            last_error_code=ErrorCode.TIMEOUT,
            last_error_at=NOW - timedelta(hours=2),
            last_attempt_at=NOW - timedelta(hours=1),
            last_attempt_success_at=NOW - timedelta(hours=1),
        ),
    )
    assert result.warnings == []
    assert result.last_error_code == ErrorCode.TIMEOUT
    assert result.completed_attempts == 4 and result.consecutive_failures == 0


def test_stale_warning_threshold_and_no_attempts():
    assert view(first_claim_at=NOW - timedelta(hours=24)).warnings == ["no_recent_success"]
    assert view(first_claim_at=NOW - timedelta(hours=24, microseconds=-1)).warnings == []
    assert (
        view(
            observation=LaneObservation("unavailable", "live_profile_unavailable", NOW, None)
        ).warnings
        == []
    )


def test_live_profile_remains_unavailable_and_unprobed():
    result = view(observation=LaneObservation("unavailable", "live_profile_unavailable", NOW, None))
    assert result.execution_mode == "unavailable"
    assert result.live_available is False
    assert result.provider_quota == "unknown"


def test_unknown_stored_reason_never_becomes_a_message():
    result = view(observation=LaneObservation("unknown-state", "private-marker", NOW, None))
    assert result.reason == result.status == result.execution_mode == "unknown"
    assert "private-marker" not in result.model_dump_json()


@pytest.mark.parametrize("changes", [{"claims_today": -1}, {"daily_attempt_limit": -1}])
def test_negative_local_budget_is_rejected(changes):
    with pytest.raises(ValueError, match="Invalid local claim counters"):
        view(**changes)
