"""Shared bounds and secret rejection for owner-supplied assistant text."""

from __future__ import annotations

from app.assistant.errors import AssistantError
from app.privacy.scanner import FindingKind, scan_text

DATA_CLASSES = ("local_only", "cloud_redacted", "cloud_ok")
MESSAGE_MAX = 4000
NOTES_MAX = 4000
TEMPLATE_MAX = 16384
NAME_MAX = 80
IMPORT_MAX = 20_000
VALUE_MAX = 500
CORPUS_LIMIT = 20
SOURCE_TEXT_MAX = 2000


def require_data_class(value: str) -> str:
    """Accept only an explicit known class. There is no default."""
    if value not in DATA_CLASSES:
        raise AssistantError("assistant_data_class")
    return value


def bounded(value: str, *, limit: int, code: str = "assistant_input") -> str:
    """Reject empty and oversize text before it is stored or echoed."""
    if not isinstance(value, str) or len(value) > limit or not value.strip():
        raise AssistantError(code)
    return value.strip()


def reject_secrets(value: str, *, source: str) -> None:
    """Refuse supported secret patterns without copying the matched text."""
    if any(finding.kind == FindingKind.SECRET for finding in scan_text(value, source=source)):
        raise AssistantError("assistant_secret")
