"""Copy legacy ``assistant_turns`` into ``assistant_messages``. Never runs automatically.

Rows land as ``quarantined`` in one collection thread; the owner re-confirms them.
A privacy finding outside ``local_only`` sets ``privacy_hold`` like ``ingest_turn``
on ``deck_ui``; a reply inherits the hold of its owner row.
External ids are derived from the turn id, so a second run inserts nothing and
writes no audit row. ``assistant_turns`` stays unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.assistant.records import AssistantTurn
from app.core.schemas import DataClass
from app.memory.audit import record_conversation_event
from app.memory.ingest import contains_secret, privacy_hold_for, text_digest
from app.memory.models import AssistantMessage, AssistantThread, ChannelAccount
from app.memory.policy import EVIDENCE_DEFAULT, ConversationError
from app.privacy.gate import strongest_hold

# Fixed id so a re-run finds the same collection thread.
COLLECTION_THREAD_ID = uuid.UUID("5a3c1d0e-6b7f-4e21-9c4d-0000000c0064")
COLLECTION_TITLE = "Sammel-Thread (Altbestand)"
_CLASSES = frozenset(item.value for item in DataClass)


@dataclass(frozen=True)
class BackfillReport:
    inserted: int
    duplicates: int
    secret_rejected: int
    thread_id: uuid.UUID | None
    privacy_held: int = 0


def _collection_thread(session: Session) -> AssistantThread:
    thread = session.get(AssistantThread, COLLECTION_THREAD_ID)
    if thread is not None:
        return thread
    # local_only is the strictest ceiling and admits every legacy class.
    thread = AssistantThread(
        id=COLLECTION_THREAD_ID,
        title=COLLECTION_TITLE,
        title_origin="assistant_proposed",
        data_class=DataClass.LOCAL_ONLY.value,
        status="proposed",
    )
    session.add(thread)
    session.flush()
    return thread


def _insert(
    session: Session,
    *,
    turn: AssistantTurn,
    role: str,
    text: str,
    suffix: str,
    thread_id: uuid.UUID,
    reply_for: uuid.UUID | None,
    hold: str | None,
) -> uuid.UUID | None:
    return session.scalar(
        pg_insert(AssistantMessage)
        .values(
            id=uuid.uuid4(),
            thread_id=thread_id,
            external_thread_id="backfill",
            external_message_id=f"assistant_turn:{turn.id}:{suffix}",
            role=role,
            text=text,
            text_sha256=text_digest(text),
            data_class=turn.data_class,
            state="quarantined",
            reply_for_message_id=reply_for,
            in_reply_to_message_id=reply_for,
            source_date=turn.created_at,
            source_channel="deck_ui",
            source_account="owner",
            source_ref=f"backfill:assistant_turns/{turn.id}/{suffix}",
            method="deck_ui",
            evidence_status=EVIDENCE_DEFAULT,
            privacy_hold=hold,
        )
        .on_conflict_do_nothing()
        .returning(AssistantMessage.id)
    )


def _existing(session: Session, turn: AssistantTurn, suffix: str) -> uuid.UUID | None:
    return session.scalar(
        select(AssistantMessage.id).where(
            AssistantMessage.source_channel == "deck_ui",
            AssistantMessage.source_account == "owner",
            AssistantMessage.external_message_id == f"assistant_turn:{turn.id}:{suffix}",
        )
    )


def backfill_assistant_turns(session: Session) -> BackfillReport:
    """Copy every legacy turn as an owner message plus its Deck reply. Idempotent."""
    turns = list(
        session.scalars(select(AssistantTurn).order_by(AssistantTurn.created_at, AssistantTurn.id))
    )
    if not turns:
        return BackfillReport(0, 0, 0, None)
    # Account first, like ingest and the downgrade, before any thread or message write.
    account = session.scalar(
        select(ChannelAccount.channel)
        .where(ChannelAccount.channel == "deck_ui", ChannelAccount.account_label == "owner")
        .with_for_update(read=True)
    )
    if account is None:
        raise ConversationError("account_missing")
    thread_existed = session.get(AssistantThread, COLLECTION_THREAD_ID) is not None
    thread = _collection_thread(session)
    inserted = duplicates = rejected = held = 0
    for turn in turns:
        if turn.data_class not in _CLASSES:
            rejected += 1
            continue
        owner_id: uuid.UUID | None = None
        # Computed even when the owner text is a secret: the reply still inherits it.
        owner_hold = privacy_hold_for("deck_ui", turn.data_class, (turn.owner_text,))
        if contains_secret(turn.owner_text):
            rejected += 1
        else:
            owner_id = _insert(
                session,
                turn=turn,
                role="owner",
                text=turn.owner_text,
                suffix="owner",
                thread_id=thread.id,
                reply_for=None,
                hold=owner_hold,
            )
            if owner_id is None:
                duplicates += 1
                owner_id = _existing(session, turn, "owner")
            else:
                inserted += 1
                held += owner_hold is not None
        if contains_secret(turn.reply_text):
            rejected += 1
            continue
        reply_hold = strongest_hold(
            privacy_hold_for("deck_ui", turn.data_class, (turn.reply_text,)), owner_hold
        )
        reply_id = _insert(
            session,
            turn=turn,
            role="assistant",
            text=turn.reply_text,
            suffix="reply",
            thread_id=thread.id,
            reply_for=owner_id,
            hold=reply_hold,
        )
        if reply_id is None:
            duplicates += 1
        else:
            inserted += 1
            held += reply_hold is not None
    if inserted or not thread_existed:
        record_conversation_event(
            session,
            "assistant.backfill_completed",
            {
                "thread_id": str(thread.id),
                "inserted": inserted,
                "duplicates": duplicates,
                "secret_rejected": rejected,
                "privacy_held": held,
            },
        )
    return BackfillReport(inserted, duplicates, rejected, thread.id, held)
