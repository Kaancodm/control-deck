"""Ordinary owner dialogue. A sentence here does not create a task."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import LOOPBACK_PREFIX, OllamaDialogueAdapter
from app.assistant.providers import MockModel, ModelAdapter, ModelReply, resolve_model
from app.assistant.records import AssistantProfileVersion, AssistantTurn
from app.assistant.text import MESSAGE_MAX, NOTES_MAX, bounded, reject_secrets, require_data_class

_RULES = (
    "Dialog ohne Werkzeuge. Keine Datenklasse aendern, kein Profil schreiben, "
    "keine Freigabe und keine Aktion."
)
_ORIGINS = frozenset({"mock", "ollama_loopback"})


@dataclass(frozen=True)
class DialogueReply:
    answer: str
    origin: str
    evidence_status: str
    data_class: str
    persisted: bool
    profile_written: bool


def compose_dialogue_prompt(
    *,
    message: str,
    profile_notes: str | None,
    history: Sequence[tuple[str, str]],
) -> str:
    """Build one bounded prompt. Profile and history are included only when supplied."""
    parts = [_RULES, ""]
    if profile_notes and profile_notes.strip():
        notes = profile_notes.strip()
        reject_secrets(notes, source="dialogue_profile")
        if len(notes) > 800:
            notes = notes[:800]
        parts.extend(["Profil, nur Kontext:", notes, ""])
    for owner_text, reply_text in tuple(history)[-4:]:
        if not isinstance(owner_text, str) or not isinstance(reply_text, str):
            raise AssistantError("assistant_input")
        reject_secrets(owner_text, source="dialogue_history")
        reject_secrets(reply_text, source="dialogue_history")
        parts.append("Owner: " + " ".join(owner_text.split())[:300])
        parts.append("Antwort: " + " ".join(reply_text.split())[:300])
    parts.append("Owner: " + message)
    prompt = "\n".join(parts)
    if len(prompt) > MESSAGE_MAX:
        raise AssistantError("assistant_oversize")
    reject_secrets(prompt, source="dialogue_prompt")
    return prompt


def reply_to_owner(
    message: str,
    *,
    data_class: str,
    model: ModelAdapter | None = None,
    profile_notes: str | None = None,
    history: Sequence[tuple[str, str]] = (),
) -> DialogueReply:
    """Answer in dialogue. The mock ignores context. Loopback sends only an opted-in prompt."""
    cleaned = bounded(message, limit=MESSAGE_MAX)
    checked = require_data_class(data_class)
    reject_secrets(cleaned, source="dialogue")
    adapter = model if model is not None else resolve_model()
    if type(adapter) is MockModel:
        produced = adapter.complete(cleaned, data_class=checked)
        if not _mock_reply_ok(produced, checked):
            raise AssistantError("live_model_disabled")
        return _reply(produced, origin="mock", evidence_status="mock")
    if type(adapter) is OllamaDialogueAdapter:
        prompt = compose_dialogue_prompt(
            message=cleaned, profile_notes=profile_notes, history=history
        )
        produced = adapter.complete(prompt, data_class=checked)
        if not _loopback_reply_ok(produced, checked):
            raise AssistantError("live_model_disabled")
        if not produced.text.startswith(LOOPBACK_PREFIX):
            raise AssistantError("assistant_model_unavailable")
        return _reply(produced, origin="ollama_loopback", evidence_status="loopback_unverified")
    raise AssistantError("live_model_disabled")


def _mock_reply_ok(produced: ModelReply, data_class: str) -> bool:
    return (
        produced.profile == "mock"
        and not produced.profile_written
        and not produced.live
        and produced.network_calls == 0
        and produced.loopback_calls == 0
        and not produced.uncensored_claim
        and produced.model_identity == "not_applicable"
        and produced.data_class == data_class
    )


def _loopback_reply_ok(produced: ModelReply, data_class: str) -> bool:
    return (
        produced.profile == "ollama_loopback"
        and not produced.profile_written
        and not produced.live
        and produced.network_calls == 0
        and produced.loopback_calls == 1
        and not produced.uncensored_claim
        and produced.model_identity == "unverified"
        and produced.data_class == data_class
    )


def _reply(produced: ModelReply, *, origin: str, evidence_status: str) -> DialogueReply:
    return DialogueReply(
        answer=produced.text,
        origin=origin,
        evidence_status=evidence_status,
        data_class=produced.data_class,
        persisted=False,
        profile_written=False,
    )


def store_turn(session: Session, reply: DialogueReply, *, owner_text: str) -> DialogueReply:
    """Persist one exchange only after the owner opted in."""
    if reply.origin not in _ORIGINS:
        raise AssistantError("assistant_input")
    session.add(
        AssistantTurn(
            data_class=reply.data_class,
            owner_text=owner_text,
            reply_text=reply.answer,
            reply_origin=reply.origin,
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
