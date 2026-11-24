"""Project existing task truth. This does not invent a channel receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CHANNEL_GAPS = (
    "Die Kanal-Quittung nach 60 Sekunden ist gebaut; ein Zustell-Transport ist nicht gebaut.",
    "Ein extern beendeter Runner schreibt kein Zeitlimit-Resultat.",
)

_FAILURE_LABELS = {
    "worker_unavailable": "Worker nicht da",
    "auth_required": "Anmeldung fehlt",
    "rate_limited": "Limit",
    "timeout": "Zeitlimit",
    "policy_blocked": "Policy blockiert",
    "acceptance_failed": "Abnahme offen",
    "internal": "Interner Fehler",
}


@dataclass(frozen=True)
class OrderView:
    code: str
    label: str
    running: bool
    finished: bool
    sent: bool


def heartbeat_is_current(*, expires_at: datetime | None, now: datetime) -> bool:
    """A lease heartbeat is current only while its stored expiry is still ahead."""
    return expires_at is not None and expires_at > now


def project_order(
    *,
    status: str,
    event_types: tuple[str, ...],
    heartbeat_current: bool,
    result_approved: bool,
    failure_code: str | None = None,
) -> OrderView:
    """Map stored state to a label. Review, a stale start, and a missing approval stay open."""
    started = "task.start" in event_types
    if status == "awaiting_approval":
        return OrderView("freigabe_offen", "Freigabe offen", False, False, False)
    if status == "queued":
        return OrderView("gesendet", "Gesendet", False, False, True)
    if status == "leased":
        return OrderView("angekommen", "Angekommen", False, False, True)
    if status == "running" and started and heartbeat_current:
        return OrderView("laeuft", "Läuft", True, False, True)
    if status == "running" and started:
        return OrderView(
            "start_ohne_herzschlag", "Start ohne aktuellen Herzschlag", False, False, True
        )
    if status == "running":
        return OrderView("zustand_ohne_start", "Zustand ohne Start", False, False, True)
    if status == "review":
        return OrderView("pruefung_offen", "Prüfung offen", False, False, True)
    if status == "done" and result_approved:
        return OrderView("fertig", "Fertig", False, True, True)
    if status == "done":
        return OrderView(
            "abschluss_ohne_freigabe", "Abschluss ohne Freigabebeleg", False, False, True
        )
    if status == "failed":
        detail = _FAILURE_LABELS.get(failure_code or "", "Fehler")
        return OrderView("fehlgeschlagen", f"Fehlgeschlagen: {detail}", False, False, True)
    if status == "blocked":
        return OrderView("blockiert", "Blockiert", False, False, False)
    if status == "cancelled":
        return OrderView("abgebrochen", "Abgebrochen", False, False, False)
    if status == "expired":
        return OrderView("abgelaufen", "Abgelaufen", False, False, False)
    if status == "succeeded":
        return OrderView("historisch", "Historischer Abschluss", False, False, False)
    return OrderView("unbekannt", "Unbekannter Zustand", False, False, False)


def safe_failure_label(code: str | None) -> str:
    """Return a fixed label. Unknown codes are not copied into the view."""
    if code in _FAILURE_LABELS:
        return _FAILURE_LABELS[code]
    return "Fehler"
