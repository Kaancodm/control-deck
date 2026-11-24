"""Phone progress is derived from stored facts and names a simulation as such."""

import uuid

from app.channels.receipt import MissingChannel, classify_delivery
from app.core.schemas import (
    ATTEMPT_TIMEOUT_DEFAULT_S,
    ATTEMPT_TIMEOUT_MAX_S,
    Policy,
)
from app.ui.progress import attempt_progress, channel_label
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


def test_channel_receipt_is_not_worker_start_or_invented_delivery() -> None:
    assert channel_label(status=None, transport=None) == "Keine Kanal-Quittung"
    assert channel_label(status="accepted", transport="missing") == "Angenommen, Transport fehlt"
    assert channel_label(status="accepted", transport="desk") == "Angenommen, Zustellung offen"
    assert channel_label(status="deadline_elapsed", transport="missing") == "Frist ohne Zustellung"
    assert channel_label(status="delivered", transport="missing") == "Keine Kanal-Quittung"
    assert channel_label(status="delivered", transport="desk") == "Zugestellt"
    assert classify_delivery(transport="missing", reported=True) == ("accepted", "missing", False)
    assert classify_delivery(transport="desk", reported=False) == ("accepted", "desk", False)
    assert classify_delivery(transport="desk", reported=True) == ("delivered", "desk", True)
    assert MissingChannel().deliver(uuid.uuid4()) is False
    waiting = attempt_progress(
        status="queued",
        current_attempt=0,
        started_attempts=(),
        result_attempt=None,
        result_status=None,
        error_code=None,
        execution_profile=None,
        fallback_pending=False,
        channel_status="accepted",
        channel_transport="missing",
    )
    assert waiting.channel == "Angenommen, Transport fehlt"
    assert waiting.acknowledgement == "Worker noch nicht gestartet"


def test_missing_start_on_an_active_lease_is_not_an_acknowledgement() -> None:
    view = attempt_progress(
        status="running",
        current_attempt=1,
        started_attempts=(),
        result_attempt=None,
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
        current_attempt=2,
        started_attempts=(1,),
        result_attempt=1,
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
        current_attempt=1,
        started_attempts=(1,),
        result_attempt=1,
        result_status="failed",
        error_code="provider-said-secret",
        execution_profile="live",
        fallback_pending=False,
    )
    assert view.outcome == "Fehler"
    assert "provider-said-secret" not in view.outcome
    assert "provider-said-secret" not in view.note
    assert view.note == "Live-Versuch"


def test_running_fallback_does_not_present_the_previous_failure_as_current() -> None:
    view = attempt_progress(
        status="running",
        current_attempt=2,
        started_attempts=(1,),
        result_attempt=1,
        result_status="failed",
        error_code="provider-said-secret",
        execution_profile="simulation",
        fallback_pending=False,
    )
    assert view.acknowledgement == "Keine Quittung"
    assert view.outcome == "Noch offen"
    assert "Fehler" not in view.outcome
    assert view.note == "Vorheriger Versuch: Fehler"
    assert "provider-said-secret" not in view.note
    assert "Simulation" not in view.note


def test_later_success_is_labelled_as_a_later_attempt() -> None:
    view = attempt_progress(
        status="review",
        current_attempt=2,
        started_attempts=(1, 2),
        result_attempt=2,
        result_status="succeeded",
        error_code=None,
        execution_profile="simulation",
        fallback_pending=False,
    )
    assert view.acknowledgement == "Worker gestartet"
    assert view.outcome == "Ergebnis da"
    assert "Späterer Versuch" in view.note
    assert "Simulation, kein Anbieter-Nachweis" in view.note
    assert "Nächste passende Lane" not in view.note
