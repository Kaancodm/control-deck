"""Ordinary owner dialogue. A sentence here does not create a task."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.errors import AssistantError
from app.assistant.providers import MockModel, ModelAdapter, ModelReply, resolve_model
from app.assistant.records import AssistantProfileVersion, AssistantTurn
from app.assistant.text import MESSAGE_MAX, NOTES_MAX, bounded, reject_secrets, require_data_class


@dataclass(frozen=True)
class DialogueReply:
    answer: str
    origin: str
    evidence_status: str
    data_class: str
    persisted: bool
    profile_written: bool


def reply_to_owner(
    message: str,
    *,
    data_class: str,
    model: ModelAdapter | None = None,
) -> DialogueReply:
    """Answer from the mock. Profile writes and approvals are not available here."""
    cleaned = bounded(message, limit=MESSAGE_MAX)
    checked = require_data_class(data_class)
    reject_secrets(cleaned, source="dialogue")
    adapter = model if model is not None else resolve_model()
    if type(adapter) is not MockModel:
        raise AssistantError("live_model_disabled")
    produced: ModelReply = adapter.complete(cleaned, data_class=checked)
    if (
        produced.profile != "mock"
        or produced.profile_written
        or produced.live
        or produced.network_calls != 0
        or produced.uncensored_claim
        or produced.data_class != checked
    ):
        raise AssistantError("live_model_disabled")
    return DialogueReply(
        answer=produced.text,
        origin="mock",
        evidence_status="mock",
        data_class=produced.data_class,
        persisted=False,
        profile_written=False,
    )


def store_turn(session: Session, reply: DialogueReply, *, owner_text: str) -> DialogueReply:
    """Persist one exchange only after the owner opted in."""
    session.add(
        AssistantTurn(
            data_class=reply.data_class,
            owner_text=owner_text,
            reply_text=reply.answer,
            reply_origin="mock",
        )
    )
    session.flush()
    return DialogueReply(
        answer=reply.answer,
        origin=reply.origin,
        evidence_status=reply.evidence_status,
        data_class=reply.data_class,
        persisted=True,
        profile_written=False,
    )


def recent_turns(session: Session, *, limit: int = 20) -> list[AssistantTurn]:
    rows = list(
        session.scalars(
            select(AssistantTurn).order_by(AssistantTurn.created_at.desc()).limit(limit)
        )
    )
    rows.reverse()
    return rows


def write_profile(session: Session, notes: str, *, actor: str) -> AssistantProfileVersion:
    """Append an owner profile version. Model output cannot call this successfully."""
    if actor != "owner":
        raise AssistantError("profile_owner_only")
    cleaned = notes.strip()
    if len(notes) > NOTES_MAX or len(cleaned) > NOTES_MAX:
        raise AssistantError("assistant_input")
    reject_secrets(cleaned, source="profile")
    current = session.scalar(
        select(AssistantProfileVersion.version)
        .order_by(AssistantProfileVersion.version.desc())
        .limit(1)
    )
    version = 1 if current is None else int(current) + 1
    row = AssistantProfileVersion(version=version, notes=cleaned)
    session.add(row)
    session.flush()
    return row


def current_profile(session: Session) -> AssistantProfileVersion | None:
    return session.scalar(
        select(AssistantProfileVersion).order_by(AssistantProfileVersion.version.desc()).limit(1)
    )
