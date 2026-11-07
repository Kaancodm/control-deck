"""Phone progress is derived from stored facts and names a simulation as such."""

from app.core.schemas import (
    ATTEMPT_TIMEOUT_DEFAULT_S,
    ATTEMPT_TIMEOUT_MAX_S,
    Policy,
)
from app.ui.progress import attempt_progress
from app.workers.lanes.claude import ClaudeExecutor
from app.workers.lanes.codex import CodexExecutor
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation


def test_adapter_cap_does_not_undercut_the_policy_default() -> None:
    assert Policy().timeout_s == ATTEMPT_TIMEOUT_DEFAULT_S == 900
    ollama = OllamaExecutor(
        profile="simulation", simulation=OllamaSimulation(models=("fixture-model",))
    )
    assert ClaudeExecutor().timeout_s == CodexExecutor().timeout_s == ollama.timeout_s
    assert ollama.timeout_s == ATTEMPT_TIMEOUT_MAX_S
    assert min(Policy().timeout_s, ollama.timeout_s) == 900


def test_missing_start_on_an_active_lease_is_not_an_acknowledgement() -> None:
    view = attempt_progress(
        status="running",
        event_types=("task.acquire",),
        result_status=None,
        error_code=None,
        execution_profile=None,
        fallback_pending=False,
    )
    assert view.intake == "Gespeichert"
    assert view.acknowledgement == "Keine Quittung"
    assert view.outcome == "Noch offen"
    assert view.note == ""


def test_timeout_names_the_failure_and_labels_simulation() -> None:
    view = attempt_progress(
        status="queued",
        event_types=("task.start", "task.retry"),
        result_status="failed",
        error_code="timeout",
        execution_profile="simulation",
        fallback_pending=True,
    )
    assert view.acknowledgement == "Worker gestartet"
    assert view.outcome == "Fehler: Zeitlimit"
    assert "Simulation, kein Anbieter-Nachweis" in view.note
    assert "Nächste passende Lane ist vorgemerkt" in view.note


def test_unknown_error_code_is_not_copied_into_the_label() -> None:
    view = attempt_progress(
        status="failed",
        event_types=("task.start",),
        result_status="failed",
        error_code="provider-said-secret",
        execution_profile="live",
        fallback_pending=False,
    )
    assert view.outcome == "Fehler"
    assert "provider-said-secret" not in view.outcome
    assert view.note == "Live-Versuch"
