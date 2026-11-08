"""Local observations never manufacture provider readiness or quota information."""

from datetime import UTC, datetime, timedelta

import pytest

from app.audit.health import AttemptHealth, LaneObservation, completed_attempt, lane_health_view
from app.core.schemas import ErrorCode, ResultStatus, Worker

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


def test_three_failed_attempts_then_success_reset_only_the_series():
    state = AttemptHealth()
    for index in range(3):
        state = completed_attempt(
            state,
            status=ResultStatus.FAILED,
            error_code=ErrorCode.TIMEOUT,
            execution_profile="live",
            recorded_at=NOW + timedelta(seconds=index),
        )
    assert state.completed_attempts == state.consecutive_failures == 3
    failed_at = state.last_error_at
    recovered = completed_attempt(
        state,
        status=ResultStatus.SUCCEEDED,
        error_code=None,
        execution_profile="live",
        recorded_at=NOW + timedelta(seconds=3),
    )
    assert recovered.completed_attempts == 4 and recovered.consecutive_failures == 0
    assert recovered.last_error_code == ErrorCode.TIMEOUT
    assert recovered.last_error_at == failed_at
    assert recovered.last_attempt_success_at == NOW + timedelta(seconds=3)


def test_cancellation_is_neither_failure_nor_success():
    previous = AttemptHealth(
        completed_attempts=2,
        consecutive_failures=2,
        last_error_code=ErrorCode.ACCEPTANCE_FAILED,
        last_error_at=NOW,
    )
    result = completed_attempt(
        previous,
        status=ResultStatus.CANCELLED,
        error_code=None,
        execution_profile="live",
        recorded_at=NOW + timedelta(seconds=1),
    )
    assert result.completed_attempts == 3 and result.consecutive_failures == 2
    assert result.last_error_at == NOW and result.last_attempt_success_at is None


@pytest.mark.parametrize("code", list(ErrorCode))
def test_safe_final_error_code_is_retained(code):
    result = completed_attempt(
        AttemptHealth(),
        status=ResultStatus.FAILED,
        error_code=code,
        execution_profile="live",
        recorded_at=NOW,
    )
    assert result.last_error_code == code and result.last_error_at == NOW


def test_unclassified_blocked_result_does_not_invent_success():
    result = completed_attempt(
        AttemptHealth(),
        status=ResultStatus.BLOCKED,
        error_code=None,
        execution_profile="live",
        recorded_at=NOW,
    )
    assert result.last_error_code is None
    assert result.consecutive_failures == 1 and result.last_attempt_success_at is None


def test_naive_attempt_timestamp_is_rejected():
    with pytest.raises(ValueError, match="Invalid completed attempt metadata"):
        completed_attempt(
            AttemptHealth(),
            status=ResultStatus.SUCCEEDED,
            error_code=None,
            execution_profile="live",
            recorded_at=datetime(2026, 1, 1),
        )


@pytest.mark.parametrize("profile", ["simulation", "remote", "unknown"])
@pytest.mark.parametrize("status", list(ResultStatus))
def test_nonlive_attempt_preserves_provenance_and_records_failures(profile, status):
    previous = AttemptHealth(
        completed_attempts=5,
        consecutive_failures=2,
        simulated_attempts=3,
        last_error_code=ErrorCode.TIMEOUT,
        last_error_at=NOW,
        last_attempt_at=NOW,
        last_attempt_success_at=NOW - timedelta(days=1),
    )
    recorded = NOW + timedelta(seconds=1)
    actual = completed_attempt(
        previous,
        status=status,
        error_code=ErrorCode.INTERNAL if status == ResultStatus.FAILED else None,
        recorded_at=recorded,
        execution_profile=profile,
    )
    assert actual.simulated_attempts == (4 if profile == "simulation" else 3)
    assert actual.remote_attempts == (1 if profile == "remote" else 0)
    assert actual.unknown_attempts == (1 if profile == "unknown" else 0)
    assert actual.completed_attempts == previous.completed_attempts
    assert actual.last_attempt_at == previous.last_attempt_at
    assert actual.last_attempt_success_at == previous.last_attempt_success_at
    failed = status in {ResultStatus.FAILED, ResultStatus.BLOCKED}
    assert actual.consecutive_failures == (3 if failed else 2)
    assert actual.last_error_at == (recorded if failed else NOW)
    assert actual.last_error_code == (
        ErrorCode.INTERNAL
        if status == ResultStatus.FAILED
        else None
        if failed
        else ErrorCode.TIMEOUT
    )


@pytest.mark.parametrize("status", ["unavailable", "auth_required", "paused", "unknown-state"])
def test_live_ready_with_non_ok_status_never_advertises_live(status):
    result = view(observation=LaneObservation(status, "live_ready", NOW, NOW))
    assert not result.live_available
    assert result.execution_mode != "live"


@pytest.mark.parametrize(
    ("age", "available"),
    [
        (timedelta(seconds=29, microseconds=999999), True),
        (timedelta(seconds=30), False),
        (timedelta(days=2), False),
        (timedelta(microseconds=-1), False),
    ],
)
def test_live_availability_expires_and_rejects_future_observations(age, available):
    observed = NOW - age
    result = view(observation=LaneObservation("ok", "live_ready", observed, observed))
    assert result.live_available is available


@pytest.mark.parametrize(
    "reason", ["inventory_timeout", "inventory_cancelled", "inventory_internal"]
)
def test_inventory_failure_reason_is_retained(reason):
    result = view(observation=LaneObservation("unavailable", reason, NOW, None))
    assert result.reason == reason


def test_default_profile_cannot_attest_live_success():
    actual = completed_attempt(
        AttemptHealth(), status=ResultStatus.SUCCEEDED, error_code=None, recorded_at=NOW
    )
    assert actual.simulated_attempts == 1
    assert actual.completed_attempts == 0 and actual.last_attempt_success_at is None


@pytest.mark.parametrize(
    "enabled,paused,available", [(True, False, True), (False, False, False), (True, True, False)]
)
def test_only_live_ready_can_advertise_live_availability(enabled, paused, available):
    actual = view(
        observation=LaneObservation("ok", "live_ready", NOW, NOW), enabled=enabled, paused=paused
    )
    assert actual.execution_mode == "live"
    assert actual.live_available == available


def test_health_revision_matches_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.api.routes.health import schema_head

    assert ScriptDirectory.from_config(Config("alembic.ini")).get_current_head() == schema_head()


def test_provenance_follows_login_capacity_with_one_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    migrations = ScriptDirectory.from_config(Config("alembic.ini"))
    assert migrations.get_heads() == ["20261108_0061"]
    provenance = migrations.get_revision("20261104_0018")
    assert provenance is not None
    assert provenance.down_revision == "20261104_0017"
    receipt = migrations.get_revision("20261108_0061")
    assert receipt is not None
    assert receipt.down_revision == "20261104_0018"


@pytest.mark.parametrize("profile", ["simulation", "remote", "unknown"])
@pytest.mark.parametrize("status", [ResultStatus.SUCCEEDED, ResultStatus.CANCELLED])
def test_nonlive_success_or_cancellation_preserves_failure_and_live_evidence(profile, status):
    from dataclasses import replace

    previous = AttemptHealth(
        completed_attempts=5,
        consecutive_failures=2,
        simulated_attempts=3,
        last_error_code=ErrorCode.TIMEOUT,
        last_error_at=NOW,
        last_attempt_at=NOW,
        last_attempt_success_at=NOW - timedelta(days=1),
    )
    actual = completed_attempt(
        previous,
        status=status,
        error_code=None,
        recorded_at=NOW + timedelta(seconds=1),
        execution_profile=profile,
    )
    assert actual == replace(
        previous,
        simulated_attempts=3 + (profile == "simulation"),
        remote_attempts=int(profile == "remote"),
        unknown_attempts=int(profile == "unknown"),
    )


@pytest.mark.parametrize("profile", ["simulation", "remote", "unknown", "live"])
@pytest.mark.parametrize("status", [ResultStatus.FAILED, ResultStatus.BLOCKED])
def test_failure_of_every_origin_updates_failure_evidence(profile, status):
    previous = AttemptHealth(
        completed_attempts=3,
        simulated_attempts=2,
        consecutive_failures=1,
        last_error_code=ErrorCode.TIMEOUT,
        last_error_at=NOW,
        last_attempt_at=NOW,
        last_attempt_success_at=NOW - timedelta(days=1),
    )
    recorded_at = NOW + timedelta(seconds=1)
    code = ErrorCode.ACCEPTANCE_FAILED if status == ResultStatus.FAILED else None
    actual = completed_attempt(
        previous,
        status=status,
        error_code=code,
        recorded_at=recorded_at,
        execution_profile=profile,
    )
    assert actual.consecutive_failures == 2
    assert actual.last_error_code == code and actual.last_error_at == recorded_at
    assert actual.last_attempt_success_at == previous.last_attempt_success_at
    assert actual.completed_attempts == (4 if profile == "live" else 3)
    assert actual.simulated_attempts == (3 if profile == "simulation" else 2)
    assert actual.remote_attempts == (1 if profile == "remote" else 0)
    assert actual.unknown_attempts == (1 if profile == "unknown" else 0)
    assert actual.last_attempt_at == (recorded_at if profile == "live" else NOW)


def test_only_live_success_resets_a_mixed_origin_failure_series():
    state = AttemptHealth()
    outcomes = [
        ("remote", ResultStatus.FAILED, ErrorCode.TIMEOUT, 1),
        ("simulation", ResultStatus.BLOCKED, ErrorCode.POLICY_BLOCKED, 2),
        ("unknown", ResultStatus.SUCCEEDED, None, 2),
        ("live", ResultStatus.CANCELLED, None, 2),
        ("live", ResultStatus.SUCCEEDED, None, 0),
    ]
    for index, (profile, status, code, failures) in enumerate(outcomes):
        state = completed_attempt(
            state,
            status=status,
            error_code=code,
            recorded_at=NOW + timedelta(seconds=index),
            execution_profile=profile,
        )
        assert state.consecutive_failures == failures
        if index < 4:
            assert state.last_attempt_success_at is None
    assert state.simulated_attempts == state.remote_attempts == state.unknown_attempts == 1
    assert state.completed_attempts == 2
    assert state.last_error_code == ErrorCode.POLICY_BLOCKED
    assert state.last_error_at == NOW + timedelta(seconds=1)
    assert state.last_attempt_success_at == NOW + timedelta(seconds=4)


@pytest.mark.parametrize(
    "reason",
    [
        "inventory_cancelled",
        "inventory_timeout",
        "inventory_internal",
        "rootless_required",
        "cli_version_unsupported",
    ],
)
def test_known_worker_failures_remain_visible_in_health(reason):
    actual = view(observation=LaneObservation("unavailable", reason, NOW, None))
    assert actual.reason == reason
    assert actual.live_available is False
