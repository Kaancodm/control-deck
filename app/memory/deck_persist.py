"""One owner decision stores a Deck exchange and approves both rows.

The persist checkbox covers the owner line and the Deck reply. There is no
second click. Clearing a fact bound is not part of this path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.memory.approval import decide_message, message_shown_bytes, shown_sha256
from app.memory.ingest import ingest_turn, record_deck_reply
from app.memory.models import AssistantMessage
from app.memory.policy import ConversationError
from app.privacy.gate import strongest_hold

_DECK_THREAD = "deck-dialog"


def persist_owner_exchange(
    session: Session,
    *,
    session_token: str,
    owner_text: str,
    reply_text: str,
    data_class: str,
) -> str | None:
    """Ingest the owner line and the Deck reply, then approve both.

    Returns the privacy-hold code when either row carries one. The hashes are
    the canonical shown bytes of the stored texts with decision ``approved``.
    A rejection stores nothing: the caller rolls the transaction back.
    """
    owner_id = _ingest_owner(session, owner_text=owner_text, data_class=data_class)
    reply = record_deck_reply(session, inbound_message_id=owner_id, text=reply_text)
    if reply.status != "accepted" or reply.message_id is None:
        raise ConversationError(reply.reason or "invalid_reply")
    _approve(session, owner_id, session_token)
    _approve(session, reply.message_id, session_token)
    owner = session.get(AssistantMessage, owner_id)
    answer = session.get(AssistantMessage, reply.message_id)
    return strongest_hold(
        None if owner is None else owner.privacy_hold,
        None if answer is None else answer.privacy_hold,
    )


def _ingest_owner(session: Session, *, owner_text: str, data_class: str) -> uuid.UUID:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    outcome = ingest_turn(
        session,
        {
            "schema_version": "channel_turn.v1",
            "channel": "deck_ui",
            "account": "owner",
            "external_thread_id": _DECK_THREAD,
            "external_message_id": f"deckmsg{uuid.uuid4().hex}",
            "role": "owner",
            "text": owner_text,
            "occurred_at": stamp,
            "data_class": data_class,
            "ingest_method": "deck_ui",
            "source_ref": {"ref": f"deck_ui:dialog/{uuid.uuid4().hex}"},
        },
    )
    if outcome.status != "accepted" or outcome.message_id is None:
        raise ConversationError(outcome.reason or "invalid_envelope")
    return outcome.message_id


def _approve(session: Session, message_id: uuid.UUID, session_token: str) -> None:
    message = session.get(AssistantMessage, message_id)
    if message is None:
        raise ConversationError("not_found")
    decide_message(
        session,
        message_id,
        decision="approved",
        session_token=session_token,
        shown_sha256_hex=shown_sha256(
            message_shown_bytes(message, decision="approved", mark_proven=False)
        ),
    )
