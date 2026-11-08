"""Plain-language attempt progress for a small phone screen.

The three lines are deck facts from stored events and results. They are not an
n8n acknowledgement and not a provider attestation.
"""

from __future__ import annotations

from dataclasses import dataclass

_FAILURES = {
    "timeout": "Zeitlimit",
    "worker_unavailable": "Worker nicht erreichbar",
    "rate_limited": "Rate-Limit",
    "auth_required": "Anmeldung fehlt",
    "policy_blocked": "Policy blockiert",
    "acceptance_failed": "Abnahme nicht erfüllt",
    "internal": "Interner Fehler",
}


@dataclass(frozen=True)
class AttemptProgress:
    intake: str
    acknowledgement: str
    outcome: str
    note: str


def attempt_progress(
    *,
    status: str,
    current_attempt: int,
    started_attempts: tuple[int, ...],
    result_attempt: int | None,
    result_status: str | None,
    error_code: str | None,
    execution_profile: str | None,
    fallback_pending: bool,
) -> AttemptProgress:
    """Describe the open attempt, or the stored result when nothing is running.

    A start or failure from an earlier attempt is not reported as the receipt or
    the outcome of the attempt that is leased or running now.
    """
    starts = set(started_attempts)
    in_flight = status in {"leased", "running"}
    result_is_current = result_attempt is not None and result_attempt == current_attempt
    use_result = result_status is not None and not (in_flight and not result_is_current)
    if use_result and result_attempt is not None and not result_is_current:
        started = result_attempt in starts
    else:
        started = current_attempt in starts
    shown_status = result_status if use_result else None
    shown_error = error_code if use_result else None
    shown_profile = execution_profile if use_result else None
    if started:
        acknowledgement = "Worker gestartet"
    elif in_flight:
        acknowledgement = "Keine Quittung"
    elif status == "awaiting_approval":
        acknowledgement = "Start-Freigabe offen"
    elif status == "queued":
        acknowledgement = "Worker noch nicht gestartet"
    else:
        acknowledgement = "Keine Worker-Quittung"
    if shown_status == "succeeded":
        outcome = "Ergebnis da"
    elif shown_status == "failed":
        label = _FAILURES.get(shown_error or "")
        outcome = f"Fehler: {label}" if label is not None else "Fehler"
    elif shown_status == "cancelled":
        outcome = "Abgebrochen"
    elif shown_status == "blocked":
        outcome = "Blockiert"
    else:
        outcome = "Noch offen"
    notes: list[str] = []
    if shown_profile == "simulation":
        notes.append("Simulation, kein Anbieter-Nachweis")
    elif shown_profile == "live":
        notes.append("Live-Versuch")
    elif shown_profile:
        notes.append("Herkunft nicht als Live-Nachweis gewertet")
    if use_result and result_is_current and current_attempt > 1:
        notes.append("Späterer Versuch")
    if fallback_pending:
        notes.append("Nächste passende Lane ist vorgemerkt")
    if (
        in_flight
        and not result_is_current
        and result_attempt is not None
        and result_status == "failed"
    ):
        label = _FAILURES.get(error_code or "")
        notes.append(
            f"Vorheriger Versuch: {label}" if label is not None else "Vorheriger Versuch: Fehler"
        )
    return AttemptProgress(
        intake="Gespeichert",
        acknowledgement=acknowledgement,
        outcome=outcome,
        note=" ".join(notes),
    )
