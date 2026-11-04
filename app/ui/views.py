"""Escaped view data and centrally registered Jinja templates."""

from __future__ import annotations

from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, Response

from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE, token_digest

TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
STATUS_LABELS = {
    "awaiting_approval": ("◷", "Start-Freigabe"),
    "queued": ("○", "Wartend"),
    "leased": ("↗", "Zugewiesen"),
    "running": ("▶", "Läuft"),
    "review": ("◇", "Review"),
    "done": ("✓", "Erledigt"),
    "failed": ("!", "Fehlgeschlagen"),
    "blocked": ("⛔", "Blockiert"),
    "cancelled": ("×", "Abgebrochen"),
    "expired": ("⌛", "Deadline abgelaufen"),
    "succeeded": ("✓", "Historisch erfolgreich"),
}
# Display names only; form values and API payloads keep the task.v1 identifiers.
DATA_CLASS_LABELS = {
    "local_only": "Nur lokal",
    "cloud_redacted": "Cloud geschwärzt",
    "cloud_ok": "Cloud ok",
}
# Every UI error code maps to (what is wrong, what to do). Codes are fixed strings chosen
# by the server, never raw input, so rendering them is safe.
ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "login_required": ("Du bist nicht angemeldet.", "Melde dich an und wiederhole die Aktion."),
    "invalid_password": ("Das Passwort ist falsch.", "Bitte erneut eingeben."),
    "login_throttled": (
        "Zu viele Anmeldeversuche in kurzer Zeit.",
        "Kurz warten und dann erneut anmelden.",
    ),
    "login_locked": (
        "Die Anmeldung ist gesperrt.",
        "Lokal mit make unlock-login entsperren. Ein Neustart hebt die Sperre nicht auf.",
    ),
    "invalid_csrf": (
        "Das Formular ist abgelaufen oder stammt nicht von dieser Sitzung.",
        "Seite neu laden und die Eingabe wiederholen.",
    ),
    "invalid_form": (
        "Das Formular war unvollständig oder beschädigt.",
        "Seite neu laden und erneut absenden.",
    ),
    "duplicate_form_field": (
        "Ein Feld wurde doppelt gesendet.",
        "Seite neu laden und erneut absenden.",
    ),
    "invalid_filter": ("Ein Filterwert ist ungültig.", "Filter zurücksetzen und neu wählen."),
    "invalid_context_reference": (
        "Die Wissensreferenz ist ungültig.",
        "Referenz über die Wissenssuche neu auswählen.",
    ),
    "invalid_context_references": (
        "Die Wissensreferenzen sind ungültig.",
        "Je Zeile eine Referenz aus der Wissenssuche, maximal 16, ohne Doppelte.",
    ),
    "invalid_draft": ("Der Chat-Entwurf ist ungültig.", "Entwurf im Chat neu erzeugen."),
    "draft_too_large": ("Der Chat-Entwurf ist zu gross.", "Kürzeren Entwurf erzeugen."),
    "draft_context_forbidden": (
        "Ein Chat-Entwurf darf keine Cloud-Klasse oder Wissensreferenzen enthalten.",
        "Entwurf übernehmen und diese Felder im Formular selbst setzen.",
    ),
    "draft_files_forbidden": (
        "Ein Chat-Entwurf darf keine Dateien enthalten.",
        "Dateien im Formular anhängen.",
    ),
    "invalid_task": (
        "Der Auftrag hat die Prüfung nicht bestanden.",
        "Pflichtfelder, Fallback-Reihenfolge (ollama, claude, codex) und Abnahmeregeln prüfen.",
    ),
    "submission_rejected": (
        "Der Auftrag wurde abgewiesen.",
        "Datenklasse, Dateien und Abnahmeregeln prüfen. Eingaben werden aus Sicherheitsgründen "
        "nicht erneut angezeigt.",
    ),
    "cloud_confirmation_required": (
        "Für eine Cloud-Datenklasse fehlt die Bestätigung.",
        "Unter Mehr Optionen den Cloud-Versand ausdrücklich bestätigen oder Nur lokal wählen.",
    ),
    "configuration_unavailable": (
        "Die lokale Konfiguration ist unvollständig.",
        "Datenschranke und Router-Konfiguration auf dem Host prüfen, dann erneut absenden.",
    ),
    "unsupported_file_type": (
        "Ein Dateityp ist nicht erlaubt.",
        "Nur UTF-8-Text, Markdown, JSON oder CSV anhängen.",
    ),
    "file_too_large": ("Eine Datei ist zu gross.", "Maximal 1 MiB je Datei und 5 MiB je Auftrag."),
    "too_many_files": ("Zu viele Dateien.", "Maximal 16 Dateien je Auftrag."),
    "invalid_utf8": (
        "Eine Datei ist kein gültiger UTF-8-Text.",
        "Datei als UTF-8 speichern und erneut anhängen.",
    ),
    "binary_input": ("Eine Datei enthält Binärdaten.", "Nur Textdateien anhängen."),
    "unsafe_directory": (
        "Der Ablageordner ist nicht sicher.",
        "Inbox-Konfiguration auf dem Host prüfen.",
    ),
    "file_changed": ("Eine Datei hat sich während der Verarbeitung geändert.", "Erneut absenden."),
    "file_unavailable": (
        "Die Datei ist nicht verfügbar.",
        "Auftrag öffnen und die Zeitlinie prüfen.",
    ),
    "invalid_attempt": (
        "Diesen Versuch gibt es nicht.",
        "Versuchsnummer in der Auftragsansicht prüfen.",
    ),
    "unsafe_artifact": ("Das Artefakt ist nicht sicher lesbar.", "Zeitlinie des Auftrags prüfen."),
    "artifact_unavailable": ("Das Artefakt ist nicht vorhanden.", "Versuch und Auftrag prüfen."),
    "confirmation_required": ("Die Bestätigung fehlt.", "Kästchen anhaken und erneut absenden."),
    "data_class_required": ("Die Datenklasse fehlt.", "Datenklasse ausdrücklich wählen."),
    "reason_required": (
        "Die Begründung fehlt oder ist zu kurz.",
        "Mindestens 10 Zeichen eingeben.",
    ),
    "invalid_action": (
        "Diese Aktion gibt es nicht.",
        "Zur Auftragsansicht zurück und eine angebotene Aktion wählen.",
    ),
    "action_unavailable": (
        "Die Aktion passt nicht zum aktuellen Zustand des Auftrags.",
        "Auftrag neu laden; der Zustand hat sich inzwischen geändert.",
    ),
    "stop_confirmation_required": (
        "Für den Not-Aus fehlt STOP.",
        "Genau STOP eingeben und erneut absenden.",
    ),
    "invalid_control": (
        "Dieser Steuerbefehl ist ungültig.",
        "Nur die angebotenen Schalter verwenden.",
    ),
    "chat_input_limit": ("Die Nachricht ist zu lang.", "Maximal 16000 Zeichen."),
    "chat_input_rejected": ("Die Nachricht wurde abgewiesen.", "Inhalt kürzen oder umformulieren."),
    "chat_lane_forbidden": (
        "Der gewählte Kontext ist für den Chat nicht erlaubt.",
        "Nur erledigte lokale Aufträge wählen.",
    ),
    "local_chat_unavailable": (
        "Der lokale Chat ist nicht erreichbar.",
        "Ollama-Profil auf dem Host prüfen.",
    ),
    "live_profile_unavailable": (
        "Kein Live-Profil eingerichtet.",
        "Inbetriebnahme gemäss Betriebshandbuch.",
    ),
    "preview_unavailable": ("Die Vorschau ist abgelaufen.", "Chat-Entwurf erneut übernehmen."),
    "temporarily_unavailable": (
        "Der Dienst ist vorübergehend nicht verfügbar.",
        "Kurz warten und neu laden.",
    ),
}
TEMPLATES.env.globals["status_labels"] = STATUS_LABELS
TEMPLATES.env.globals["data_class_labels"] = DATA_CLASS_LABELS
TEMPLATES.env.globals["error_messages"] = ERROR_MESSAGES
TEMPLATES.env.globals["result_labels"] = {
    "succeeded": ("✓", "Erfolgreich"),
    "failed": ("!", "Fehlgeschlagen"),
    "blocked": ("⛔", "Blockiert"),
    "cancelled": ("×", "Abgebrochen"),
}

# Single-use PRG handoffs keep chat and draft content out of URLs, cookies and the DB.
_PREVIEW_TTL = 300
_PREVIEW_LIMIT = 32
_previews: dict[str, tuple[str, str, float, dict[str, Any]]] = {}
_preview_lock = Lock()


def _prune_previews() -> None:
    now = monotonic()
    for key, (_, _, expires, _) in list(_previews.items()):
        if expires <= now:
            del _previews[key]


def preview_redirect(request: Request, path: str, **context: Any) -> Response:
    """Hold a bounded, short-lived preview in this process until its redirected GET."""
    token = token_urlsafe(32)
    session_hash = token_digest(request.cookies[SESSION_COOKIE])
    assert session_hash is not None
    with _preview_lock:
        _prune_previews()
        while len(_previews) >= _PREVIEW_LIMIT:
            del _previews[next(iter(_previews))]
        _previews[token] = (path, session_hash, monotonic() + _PREVIEW_TTL, context)
    return RedirectResponse(f"{path}?preview={token}", status_code=303)


def preview_context(request: Request) -> dict[str, Any]:
    """Only the originating owner session and destination can consume a preview."""
    token = request.query_params.get("preview")
    if token is None:
        return {}
    session_hash = token_digest(request.cookies[SESSION_COOKIE])
    if session_hash is None:
        return {"error": "preview_unavailable"}
    with _preview_lock:
        _prune_previews()
        preview = _previews.get(token)
        if preview is not None:
            path, bound_session, _, context = preview
            if path == request.url.path and bound_session == session_hash:
                del _previews[token]
                return context
    return {"error": "preview_unavailable"}


def page(request: Request, name: str, *, status: int = 200, **context: Any) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context={
            "csrf": request.cookies.get(CSRF_COOKIE, ""),
            "owner": True,
            "controls": getattr(request.state, "controls", None),
            "lane_health": getattr(request.state, "lane_health", []),
            "health_unavailable": getattr(request.state, "health_unavailable", False),
            **context,
        },
        status_code=status,
    )
