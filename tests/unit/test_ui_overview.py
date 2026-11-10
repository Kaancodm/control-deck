"""Overview projections: fixed steps, honest lane labels, derived open points, flag states."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.audit.health import LaneHealthView
from app.audit.presentation import AuditItem
from app.core.schemas import Worker
from app.db.settings import RunnerSettings
from app.db.state_machine import TaskState
from app.ui import overview
from app.ui.overview import FeatureFlags, Outcome

NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
FLAG_VARIABLES = (
    "DECK_ASSISTANT_OLLAMA",
    "DECK_OLLAMA_URL",
    "DECK_ASSISTANT_OLLAMA_MODEL",
    "DECK_ASSISTANT_WEB",
    "DECK_ASSISTANT_WEB_ALLOWLIST",
    "DECK_ASSISTANT_SEARCH_PROVIDER",
    "DECK_ASSISTANT_MEDIAWIKI_API",
    "DECK_ASSISTANT_SEARXNG_URL",
    "DECK_ROUTINE_WAKE",
    "DECK_ROUTINE_WAKE_URL",
    "DECK_ROUTINE_WAKE_AUTHORIZATION",
)


def lane(worker: Worker = Worker.OLLAMA, **values: object) -> LaneHealthView:
    base: dict[str, object] = {
        "lane": worker,
        "enabled": True,
        "paused": False,
        "status": "ok",
        "reason": "simulation_ready",
        "execution_mode": "simulation",
        "live_available": False,
        "daily_attempt_limit": 20,
        "claims_today": 3,
        "configured_attempts_remaining": 17,
        "observed_at": NOW,
        "last_observation_success_at": NOW,
        "simulated_attempts": 0,
        "remote_attempts": 0,
        "unknown_attempts": 0,
        "completed_attempts": 0,
        "consecutive_failures": 0,
        "last_error_code": None,
        "last_error_at": None,
        "last_attempt_at": None,
        "last_attempt_success_at": None,
        "warnings": [],
    }
    return LaneHealthView.model_validate(base | values)


def flags(**values: object) -> FeatureFlags:
    base: dict[str, object] = {
        "assistant_loopback": "on",
        "model_named": True,
        "assistant_web": "on",
        "encyclopedia": True,
        "web_search": False,
        "routine_wake": "on",
    }
    return FeatureFlags(**(base | values))  # type: ignore[arg-type]


@pytest.fixture
def clean_env(monkeypatch):
    for name in FLAG_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("state", list(TaskState))
def test_workflow_steps_cover_every_task_state(state: TaskState) -> None:
    steps = overview.workflow_steps(state.value)
    assert [step.number for step in steps] == ["01", "02", "03", "04", "05", "06"]
    assert [step.label for step in steps] == [
        "Geplant",
        "Start-Freigabe",
        "Wartend",
        "Zugewiesen",
        "Läuft",
        "Review",
    ]
    states = [step.state for step in steps]
    order = ["awaiting_approval", "queued", "leased", "running", "review"]
    if state.value in order:
        current = order.index(state.value) + 1
        assert states == ["done"] * current + ["current"] + ["todo"] * (5 - current)
    elif state == TaskState.DONE:
        assert states == ["done"] * 6
    else:
        # Terminal failures and historical rows highlight nothing.
        assert states == ["todo"] * 6


def test_workflow_steps_without_focus_are_a_legend() -> None:
    assert {step.state for step in overview.workflow_steps(None)} == {"todo"}


def test_claims_summary_with_and_without_limits() -> None:
    assert overview.claims_summary([]) is None
    full = overview.claims_summary(
        [lane(claims_today=2, daily_attempt_limit=10), lane(Worker.CLAUDE, claims_today=5)]
    )
    assert full == overview.Claims(total=7, limit=30)
    partial = overview.claims_summary(
        [lane(claims_today=2), lane(Worker.CODEX, claims_today=1, daily_attempt_limit=None)]
    )
    assert partial == overview.Claims(total=3, limit=None)


@pytest.mark.parametrize(
    "values,label,tone",
    [
        ({"enabled": False, "status": "auth_required"}, "Deaktiviert", "off"),
        ({"status": "auth_required", "paused": True}, "Anmeldung nötig", "warn"),
        ({"paused": True, "status": "unavailable"}, "Pausiert", "off"),
        ({"status": "paused"}, "Pausiert", "off"),
        ({"status": "unavailable", "configured_attempts_remaining": 0}, "Nicht verfügbar", "warn"),
        (
            {"configured_attempts_remaining": 0, "live_available": True},
            "Lokales Tageslimit erreicht",
            "warn",
        ),
        ({"live_available": True, "execution_mode": "live"}, "Bereit", "on"),
        ({"execution_mode": "simulation"}, "Simulation", "off"),
        ({"execution_mode": "live", "live_available": False}, "Vorprüfung ok", "on"),
        ({"status": "unknown", "execution_mode": "unknown"}, "Unbekannt", "off"),
    ],
)
def test_lane_rows_rank_labels(values: dict[str, object], label: str, tone: str) -> None:
    (row,) = overview.lane_rows([lane(**values)])
    assert (row.label, row.tone) == (label, tone)
    assert row.lane == "ollama" and row.kind == "lokal"


def test_lane_rows_keep_cli_kind_and_counters() -> None:
    (row,) = overview.lane_rows([lane(Worker.CODEX, claims_today=4, daily_attempt_limit=None)])
    assert (row.kind, row.claims, row.limit) == ("CLI", 4, None)


def audit_item(phase: str | None, minutes: int) -> AuditItem:
    return AuditItem(
        source="operation",
        id=minutes,
        created_at=NOW - timedelta(minutes=minutes),
        event_type="operation.backup",
        phase=phase,
    )


def test_latest_outcome_skips_started_and_takes_newest() -> None:
    assert overview.latest_outcome([]) is None
    assert overview.latest_outcome([audit_item("started", 1)]) is None
    outcome = overview.latest_outcome(
        [audit_item("started", 1), audit_item("failed", 2), audit_item("completed", 3)]
    )
    assert outcome == Outcome("failed", NOW - timedelta(minutes=2))


def test_open_points_follow_fixed_order_and_codes() -> None:
    points = overview.open_points(
        controls=RunnerSettings(paused=True, stop_requested=True),
        waiting_total=2,
        blocked_total=1,
        lanes=[lane(Worker.CLAUDE, warnings=["auth_required", "no_recent_success"])],
        health_unavailable=True,
        flags=flags(assistant_loopback="incomplete", assistant_web="off", routine_wake="off"),
        last_backup=None,
        last_restore=None,
    )
    assert [point.code for point in points] == [
        "stop",
        "paused",
        "approvals",
        "blocked",
        "lane_auth_required",
        "lane_no_recent_success",
        "health_unavailable",
        "assistant_loopback",
        "assistant_web",
        "routine_wake",
        "backup_missing",
        "restore_missing",
    ]
    assert points[2].title == "2 Freigaben offen"
    assert points[2].href == "/ui/tasks#waiting-tasks"
    assert points[3].href == "/ui/tasks?state=blocked"
    assert points[7].chip == "unvollständig" and points[8].chip == "aus"
    assert points[9].href == "/ui/overview#meldungen"
    assert all(point.href is None or point.href.startswith("/ui/") for point in points)


def test_open_points_are_empty_when_everything_is_recorded() -> None:
    done = Outcome("completed", NOW)
    assert (
        overview.open_points(
            controls=RunnerSettings(),
            waiting_total=0,
            blocked_total=0,
            lanes=[lane()],
            health_unavailable=False,
            flags=flags(),
            last_backup=done,
            last_restore=done,
        )
        == ()
    )
    failed = Outcome("failed", NOW)
    codes = [
        point.code
        for point in overview.open_points(
            controls=None,
            waiting_total=0,
            blocked_total=0,
            lanes=[],
            health_unavailable=False,
            flags=flags(),
            last_backup=failed,
            last_restore=failed,
        )
    ]
    assert codes == ["backup_failed", "restore_failed"]


def test_feature_flags_default_off(clean_env) -> None:
    assert overview.feature_flags() == FeatureFlags("off", False, "off", False, False, "off")


def test_feature_flags_env_matrix(clean_env) -> None:
    clean_env.setenv("DECK_ASSISTANT_OLLAMA", "1")
    clean_env.setenv("DECK_ASSISTANT_WEB", "1")
    clean_env.setenv("DECK_ROUTINE_WAKE", "1")
    state = overview.feature_flags()
    assert (state.assistant_loopback, state.assistant_web, state.routine_wake) == (
        "incomplete",
        "incomplete",
        "incomplete",
    )
    assert not state.model_named and not state.encyclopedia and not state.web_search
    clean_env.setenv("DECK_OLLAMA_URL", "http://127.0.0.1:1")
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_MODEL", "synthetic-model:tag")
    clean_env.setenv("DECK_ASSISTANT_WEB_ALLOWLIST", "example.invalid")
    clean_env.setenv("DECK_ASSISTANT_SEARCH_PROVIDER", "mediawiki")
    clean_env.setenv("DECK_ASSISTANT_MEDIAWIKI_API", "https://example.invalid/w/api.php")
    clean_env.setenv("DECK_ROUTINE_WAKE_URL", "https://example.invalid/wake")
    clean_env.setenv("DECK_ROUTINE_WAKE_AUTHORIZATION", "Bearer synthetic-wake-key")
    state = overview.feature_flags()
    assert state == FeatureFlags("on", True, "on", True, False, "on")
    clean_env.setenv("DECK_ASSISTANT_SEARCH_PROVIDER", "searxng")
    assert overview.feature_flags().assistant_web == "incomplete"
    clean_env.setenv("DECK_ASSISTANT_SEARXNG_URL", "https://example.invalid/search")
    state = overview.feature_flags()
    assert (state.assistant_web, state.encyclopedia, state.web_search) == ("on", False, True)
    clean_env.setenv("DECK_ROUTINE_WAKE_URL", "http://example.invalid/wake")
    assert overview.feature_flags().routine_wake == "incomplete"
    clean_env.setenv("DECK_ROUTINE_WAKE", "true")
    assert overview.feature_flags().routine_wake == "off"


def test_feature_flags_never_carry_values(clean_env) -> None:
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_MODEL", "sentinel-model-7411")
    state = overview.feature_flags()
    assert "sentinel" not in repr(state)
    assert all(isinstance(value, (bool, str)) for value in vars(state).values())
    assert set(vars(state).values()) <= {True, False, "on", "off", "incomplete"}


def test_network_nodes_reflect_real_states() -> None:
    nodes = overview.network_nodes(
        lanes=[lane(), lane(Worker.CLAUDE, paused=True)],
        flags=flags(assistant_loopback="off"),
        last_backup=None,
    )
    by_key = {node.key: node for node in nodes}
    assert list(by_key) == [
        "owner",
        "ollama",
        "claude",
        "codex",
        "assistant",
        "messages",
        "database",
        "backup",
    ]
    assert by_key["ollama"].active and by_key["ollama"].label == "ollama"
    assert not by_key["claude"].active and by_key["claude"].label == "claude · aus"
    assert not by_key["codex"].active
    assert not by_key["assistant"].active and by_key["messages"].active
    assert not by_key["backup"].active
    assert all(0 <= node.x <= 400 and 0 <= node.y <= 250 for node in nodes)
