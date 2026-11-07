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
    event_types: tuple[str, ...],
    result_status: str | None,
    error_code: str | None,
    execution_profile: str | None,
    fallback_pending: bool,
) -> AttemptProgress:
    """Describe intake, worker acknowledgement and result or failure."""
    started = "task.start" in event_types
    if started:
        acknowledgement = "Worker gestartet"
    elif status in {"leased", "running"}:
        acknowledgement = "Keine Quittung"
    elif status == "awaiting_approval":
        acknowledgement = "Start-Freigabe offen"
    elif status == "queued":
        acknowledgement = "Worker noch nicht gestartet"
    else:
        acknowledgement = "Keine Worker-Quittung"
    if result_status == "succeeded":
        outcome = "Ergebnis da"
    elif result_status == "failed":
        label = _FAILURES.get(error_code or "")
        outcome = f"Fehler: {label}" if label is not None else "Fehler"
    elif result_status == "cancelled":
        outcome = "Abgebrochen"
    elif result_status == "blocked":
        outcome = "Blockiert"
    else:
        outcome = "Noch offen"
    notes: list[str] = []
    if execution_profile == "simulation":
        notes.append("Simulation, kein Anbieter-Nachweis")
    elif execution_profile == "live":
        notes.append("Live-Versuch")
    elif execution_profile:
        notes.append("Herkunft nicht als Live-Nachweis gewertet")
    if fallback_pending:
        notes.append("Nächste passende Lane ist vorgemerkt")
    return AttemptProgress(
        intake="Gespeichert",
        acknowledgement=acknowledgement,
        outcome=outcome,
        note=" ".join(notes),
    )
