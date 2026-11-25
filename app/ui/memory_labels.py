"""Fixed German labels for conversation reason codes and privacy holds."""

from __future__ import annotations

import re

from app.memory.reasons import HOLD_CODES, UNKNOWN_SOURCE_CODES

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,48}\Z")

REASON_LABELS: dict[str, str] = {
    "unknown_account": "Unbekanntes Konto",
    "account_inactive": "Konto inaktiv",
    "invalid_envelope": "Umschlag ungültig",
    "data_class_block": "Datenklasse gesperrt",
    "secret_rejected": "Geheimnis abgewiesen",
    "thread_class_block": "Themenklasse gesperrt",
    "duplicate_body_mismatch": "Doppelter Text weicht ab",
    "duplicate": "Schon vorhanden",
    "invalid_method": "Methode unzulässig",
    "reply_path_required": "Antwort braucht den Antwortweg",
    "reply_channel": "Antwortkanal unzulässig",
    "reply_target_rejected": "Antwortziel abgelehnt",
    "reply_target_missing": "Antwortziel fehlt",
    "invalid_reply": "Antwort ungültig",
    "source_rejected": "Quelle abgelehnt",
    "not_decided": "Noch nicht entschieden",
    "no_change": "Keine Änderung",
    "already_corrected": "Schon korrigiert",
    "already_decided": "Schon entschieden",
    "link_conflict": "Verknüpfung im Konflikt",
    "owner_session_required": "Owner-Sitzung nötig",
    "shown_bytes_mismatch": "Anzeige stimmt nicht",
    "invalid_fact": "Faktum ungültig",
    "invalid_decision": "Entscheid ungültig",
    "invalid_title": "Titel ungültig",
    "invalid_data_class": "Datenklasse ungültig",
    "not_found": "Eintrag fehlt",
    "thread_not_active": "Thema nicht aktiv",
    "thread_unmapped": "Ohne Thema",
    "unknown_channel": "Kanal unbekannt",
    "local_only_cloud_link": "Nur-lokal passt nicht zum Cloud-Kanal",
    "account_missing": "Konto fehlt",
}
HOLD_LABELS: dict[str, str] = {
    "privacy_block": "Datenschutz-Treffer",
    "blocklist_unavailable": "Blockliste nicht verfügbar",
}
DECISION_LABELS: dict[str, str] = {"approved": "Freigegeben", "rejected": "Abgelehnt"}
REF_LABELS: dict[str, str] = {
    "message_id": "Nachricht",
    "existing_message_id": "Bestehende Nachricht",
    "reply_for_message_id": "Antwort auf",
    "fact_id": "Faktum",
    "thread_id": "Thema",
    "link_id": "Verknüpfung",
    "approval_id": "Freigabe",
    "corrects_approval_id": "Korrigiert",
}
DIGEST_LABELS: dict[str, str] = {
    "text_sha256": "Text-Hash",
    "shown_sha256": "Anzeige-Hash",
    "existing_text_sha256": "Bisheriger Text-Hash",
}
STATE_LABELS: dict[str, str] = {
    "quarantined": "Quarantäne",
    "approved": "Freigegeben",
    "rejected": "Abgelehnt",
    "proposed": "Vorgeschlagen",
    "active": "Aktiv",
}


def reason_text(code: str | None) -> str:
    """Label for a fixed reason code. Unknown tokens stay as the code, free text does not."""
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        return ""
    label = REASON_LABELS.get(code, code)
    if code in UNKNOWN_SOURCE_CODES:
        return f"{label} (Plan: unknown_source)"
    return label


def hold_text(code: str | None) -> str:
    """Fixed hold label. An unknown code is not shown."""
    if code not in HOLD_CODES:
        return ""
    return HOLD_LABELS[code]


def decision_text(code: str | None) -> str:
    if code is None:
        return ""
    return DECISION_LABELS.get(code, "")


def ref_label(key: str) -> str:
    return REF_LABELS.get(key, "")


def digest_label(key: str) -> str:
    return DIGEST_LABELS.get(key, "")
