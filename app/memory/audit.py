"""Taskless conversation audit rows in the existing append-only ``events`` table.

Detail values are restricted to ids, hashes, reason codes, enums, and counts. A
free-text key or a long string is refused, so message text cannot reach the audit.
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy.orm import Session

from app.db.models import Event
from app.memory.policy import ConversationError

_PREFIXES: Final = ("assistant.", "memory.")
_EVENT_TYPE = re.compile(r"^(assistant|memory)\.[a-z][a-z0-9_]{0,48}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_ALLOWED_KEYS: Final = frozenset(
    {
        "reason",
        "channel",
        "account",
        "role",
        "method",
        "data_class",
        "kind",
        "decision",
        "evidence_status",
        "message_id",
        "existing_message_id",
        "reply_for_message_id",
        "thread_id",
        "link_id",
        "fact_id",
        "approval_id",
        "corrects_approval_id",
        "privacy_hold",
        "text_sha256",
        "existing_text_sha256",
        "shown_sha256",
        "class_forced",
        "thread_unmapped",
        "in_reply_to_unresolved",
        "inserted",
        "duplicates",
        "secret_rejected",
        "attached",
    }
)
ACTOR_SYSTEM: Final = "system"
ACTOR_OWNER: Final = "operator"


def _safe_value(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return 0 <= value <= 2**31
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def record_conversation_event(
    session: Session,
    event_type: str,
    detail: dict[str, object],
    *,
    actor: str = ACTOR_SYSTEM,
) -> Event:
    """Append one taskless audit row. Refuses anything that could carry message text."""
    if not event_type.startswith(_PREFIXES) or not _EVENT_TYPE.fullmatch(event_type):
        raise ConversationError("invalid_audit_event")
    if actor not in (ACTOR_SYSTEM, ACTOR_OWNER):
        raise ConversationError("invalid_audit_event")
    for key, value in detail.items():
        if key not in _ALLOWED_KEYS or not _safe_value(value):
            raise ConversationError("invalid_audit_detail")
    event = Event(
        task_id=None,
        event_type=event_type,
        from_status=None,
        to_status=None,
        actor=actor,
        detail=dict(detail),
    )
    session.add(event)
    session.flush()
    return event
