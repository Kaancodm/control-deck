"""Live dialogue on the topic thread (M1b).

The deck UI writes the owner line with ``ingest_turn`` and the answer with
``record_deck_reply``. Both start quarantined. ``persist`` is a pre-approval of
the displayed deck answer. The hash on that path is formal: sha256 of the row
just saved. A reply with ``privacy_hold`` is not approved, so the mark stays
visible in quarantine. The owner line always stays quarantined. When history
is requested, the prompt comes from ``load_memory_context`` for target
``ollama`` only. This path never selects ``codex`` or a cloud target; those
filters stay inside the loader.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.dialogue import (
    DialogueReply,
    current_profile,
    reply_to_owner,
    store_turn,
)
from app.assistant.errors import AssistantError
from app.assistant.text import MESSAGE_MAX, bounded, require_data_class
from app.memory.approval import decide_message, message_shown_bytes, shown_sha256
from app.memory.context import MemoryContext, load_memory_context
from app.memory.ingest import IngestOutcome, ingest_turn, record_deck_reply
from app.memory.models import AssistantMessage, AssistantThread, ThreadLink
from app.memory.policy import ConversationError, class_admits

# Local loopback or the mock. Cloud targets and codex are not chosen here.
DIALOGUE_TARGET = "ollama"
DEFAULT_EXTERNAL_THREAD_ID = "deck"
_DECK_CHANNEL = "deck_ui"
_DECK_ACCOUNT = "owner"
_SOURCE_REF = "deck_ui:dialog"
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#+-]{0,199}\Z")
_UNPROVEN = "unbewiesen"

_INGEST_ERRORS = {
    "secret_rejected": "assistant_secret",
    "data_class_block": "assistant_data_class",
    "thread_class_block": "thread_class_block",
    "invalid_envelope": "assistant_input",
    "invalid_reply": "assistant_input",
    "unknown_account": "assistant_input",
    "account_inactive": "assistant_input",
    "invalid_method": "assistant_input",
    "reply_channel": "assistant_input",
    "reply_target_rejected": "assistant_input",
    "reply_target_missing": "assistant_input",
    "duplicate": "assistant_input",
    "duplicate_body_mismatch": "assistant_input",
}


def normalize_external_thread_id(value: str) -> str:
    """Empty means the deck dialog id. Anything else must match the envelope."""
    cleaned = value.strip()
    if not cleaned:
        return DEFAULT_EXTERNAL_THREAD_ID
    if "://" in cleaned or _EXTERNAL_ID.fullmatch(cleaned) is None:
        raise AssistantError("assistant_input")
    return cleaned


def active_deck_thread_id(session: Session, external_thread_id: str) -> uuid.UUID | None:
    """Active topic for this deck thread, or none while the link is still proposed."""
    link = session.scalar(
        select(ThreadLink).where(
            ThreadLink.channel == _DECK_CHANNEL,
            ThreadLink.account == _DECK_ACCOUNT,
            ThreadLink.external_thread_id == external_thread_id,
            ThreadLink.state == "active",
        )
    )
    if link is None:
        return None
    thread = session.get(AssistantThread, link.thread_id)
    if thread is None or thread.status != "active":
        return None
    return thread.id


def render_memory_block(context: MemoryContext) -> str | None:
    """Approved history and facts. ``unbewiesen`` is labeled; the profile is not repeated."""
    lines: list[str] = []
    for item in context.items:
        if item.kind == "profile":
            continue
        shown = item.text
        if item.evidence_status == _UNPROVEN:
            shown = f"[{_UNPROVEN}] {shown}"
        if item.kind == "history":
            lines.append(f"{item.role or 'owner'}: {shown}")
        elif item.kind == "fact":
            lines.append(f"fakt: {shown}")
    if not lines:
        return None
    return "Themen-Thread, nur freigegebene Zeilen:\n" + "\n".join(lines)


def conduct_dialogue(
    session: Session,
    *,
    message: str,
    data_class: str,
    include_profile: bool,
    include_history: bool,
    external_thread_id: str,
    persist: bool,
    on_chunk: Callable[[str], None] | None = None,
    session_token: str | None = None,
) -> DialogueReply:
    """Answer and store the exchange. ``persist`` pre-approves an unmarked reply."""
    external = normalize_external_thread_id(external_thread_id)
    checked = require_data_class(data_class)
    cleaned = bounded(message, limit=MESSAGE_MAX)
    thread_id = active_deck_thread_id(session, external)
    _ensure_thread_admits(session, thread_id, checked)
    memory_context: str | None = None
    if include_history:
        context = load_memory_context(
            session,
            thread_id=thread_id,
            target=DIALOGUE_TARGET,
            data_class=checked,
            query=cleaned,
        )
        memory_context = render_memory_block(context)
    notes: str | None = None
    if include_profile:
        profile = current_profile(session)
        notes = None if profile is None else profile.notes
    reply = reply_to_owner(
        cleaned,
        data_class=checked,
        profile_notes=notes,
        history=(),
        memory_context=memory_context,
        on_chunk=on_chunk,
    )
    inbound_id = _store_owner_turn(
        session, text=cleaned, data_class=checked, external_thread_id=external
    )
    try:
        reply_outcome = record_deck_reply(session, inbound_message_id=inbound_id, text=reply.answer)
    except ConversationError as exc:
        raise _as_assistant_error(exc.code) from None
    reply_id = _require_accepted(reply_outcome)
    if persist:
        displayed = session.get(AssistantMessage, reply_id)
        if displayed is None:
            raise ConversationError("not_found")
        # A hold stays visible in quarantine. The inbox click can still approve it.
        if displayed.privacy_hold is None:
            if session_token is None:
                raise ConversationError("owner_session_required")
            approve_displayed_reply(
                session,
                displayed.id,
                session_token=session_token,
                shown_sha256_hex=shown_sha256(
                    message_shown_bytes(displayed, decision="approved", mark_proven=False)
                ),
            )
        return store_turn(session, reply, owner_text=cleaned)
    return reply


def approve_displayed_reply(
    session: Session,
    message_id: uuid.UUID,
    *,
    session_token: str,
    shown_sha256_hex: str,
) -> AssistantMessage:
    """Approve one displayed assistant reply. A wrong or stale hash writes nothing.

    The hash is sha256 of :func:`message_shown_bytes` for ``approved`` without
    ``mark_proven``. On the persist path that digest is computed from the row
    just saved, then :func:`decide_message` checks the same bytes again. A row
    that is not an assistant reply is ``not_found`` before that check.
    ``privacy_hold`` is not cleared; the caller skips this function when a hold
    is set.
    """
    message = session.get(AssistantMessage, message_id)
    if message is None or message.role != "assistant":
        raise ConversationError("not_found")
    return decide_message(
        session,
        message.id,
        decision="approved",
        session_token=session_token,
        shown_sha256_hex=shown_sha256_hex,
        mark_proven=False,
    )


def _ensure_thread_admits(session: Session, thread_id: uuid.UUID | None, data_class: str) -> None:
    if thread_id is None:
        return
    thread = session.get(AssistantThread, thread_id)
    if thread is None or thread.status != "active":
        return
    if not class_admits(thread.data_class, data_class):
        raise AssistantError("thread_class_block")


def _store_owner_turn(
    session: Session, *, text: str, data_class: str, external_thread_id: str
) -> uuid.UUID:
    message_id = f"m-{uuid.uuid4().hex}"
    try:
        outcome = ingest_turn(
            session,
            {
                "schema_version": "channel_turn.v1",
                "channel": _DECK_CHANNEL,
                "account": _DECK_ACCOUNT,
                "external_thread_id": external_thread_id,
                "external_message_id": message_id,
                "role": "owner",
                "text": text,
                "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data_class": data_class,
                "ingest_method": _DECK_CHANNEL,
                "source_ref": {"ref": _SOURCE_REF},
            },
        )
    except ConversationError as exc:
        raise _as_assistant_error(exc.code) from None
    return _require_accepted(outcome)


def _require_accepted(outcome: IngestOutcome) -> uuid.UUID:
    if outcome.status == "accepted" and outcome.message_id is not None:
        return outcome.message_id
    raise _as_assistant_error(outcome.reason or "")


def _as_assistant_error(code: str) -> AssistantError:
    return AssistantError(_INGEST_ERRORS.get(code, "assistant_input"))
