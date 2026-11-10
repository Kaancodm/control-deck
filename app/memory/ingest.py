"""``ingest_turn``: allowlist, secret scan, dedup, quarantine, thread mapping, audit.

Every accept, reject, and duplicate writes exactly one taskless audit row. A turn with
a secret finding is not stored anywhere: only its sha256 and ``secret_rejected``
reach the audit. Nothing here sends a message to any channel.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.memory.audit import record_conversation_event
from app.memory.envelope import TEXT_MAX, ChannelTurnV1, parse_turn
from app.memory.models import (
    AssistantMessage,
    AssistantThread,
    ChannelAccount,
    ThreadLink,
)
from app.memory.policy import (
    CHANNEL_CLASS_CEILING,
    CHANNELS,
    EVIDENCE_DEFAULT,
    FORCED_CLASS,
    REPLY_CHANNELS,
    ConversationError,
    check_link_class,
    class_admits,
)
from app.privacy.scanner import FindingKind, scan_text

Status = Literal["accepted", "duplicate", "rejected"]
_ACCOUNT = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")


@dataclass(frozen=True)
class IngestOutcome:
    """Result of one ingest call. ``reason`` is a fixed code, never message text."""

    status: Status
    reason: str | None = None
    message_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None


def text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def contains_secret(*values: str | None) -> bool:
    """True when any value has a supported secret pattern. Matched text is not copied."""
    for value in values:
        if value is None:
            continue
        findings = scan_text(value, source="channel_turn")
        if any(finding.kind == FindingKind.SECRET for finding in findings):
            return True
    return False


def _reject(
    session: Session, reason: str, detail: dict[str, object], *, event: str = "turn_rejected"
) -> IngestOutcome:
    record_conversation_event(session, f"assistant.{event}", {**detail, "reason": reason})
    return IngestOutcome(status="rejected", reason=reason)


def _safe_identity(payload: object) -> dict[str, object]:
    """Channel/account for the audit of an invalid envelope, only when well-formed."""
    if not isinstance(payload, Mapping):
        return {}
    detail: dict[str, object] = {}
    channel = payload.get("channel")
    account = payload.get("account")
    if isinstance(channel, str) and channel in CHANNELS:
        detail["channel"] = channel
        if isinstance(account, str) and _ACCOUNT.fullmatch(account):
            detail["account"] = account
    text = payload.get("text")
    if isinstance(text, str):
        try:
            detail["text_sha256"] = text_digest(text)
        except UnicodeError:
            pass
    return detail


def _load_account(session: Session, channel: str, account: str) -> ChannelAccount | None:
    return session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.channel == channel, ChannelAccount.account_label == account)
        .with_for_update(read=True)
    )


def _active_link(session: Session, turn: ChannelTurnV1) -> ThreadLink | None:
    return session.scalar(
        select(ThreadLink).where(
            ThreadLink.channel == turn.channel,
            ThreadLink.account == turn.account,
            ThreadLink.external_thread_id == turn.external_thread_id,
            ThreadLink.state != "rejected",
        )
    )


def _resolve_in_reply_to(session: Session, turn: ChannelTurnV1) -> uuid.UUID | None:
    if turn.in_reply_to is None:
        return None
    return session.scalar(
        select(AssistantMessage.id).where(
            AssistantMessage.source_channel == turn.channel,
            AssistantMessage.source_account == turn.account,
            AssistantMessage.external_message_id == turn.in_reply_to,
        )
    )


def _existing_duplicate(session: Session, turn: ChannelTurnV1, digest: str) -> uuid.UUID | None:
    query = select(AssistantMessage.id).where(
        AssistantMessage.source_channel == turn.channel,
        AssistantMessage.source_account == turn.account,
    )
    if turn.external_message_id is not None:
        query = query.where(AssistantMessage.external_message_id == turn.external_message_id)
    else:
        query = query.where(
            AssistantMessage.external_message_id.is_(None),
            AssistantMessage.external_thread_id == turn.external_thread_id,
            AssistantMessage.role == turn.role,
            AssistantMessage.text_sha256 == digest,
            AssistantMessage.source_date == turn.occurred_at_value(),
        )
    return session.scalar(query)


def _proposal_title(turn: ChannelTurnV1) -> str:
    return f"{turn.channel}/{turn.account}: {turn.external_thread_id[:150]}"


def _propose_link(session: Session, turn: ChannelTurnV1, data_class: str) -> uuid.UUID | None:
    """Store one open mapping proposal with a proposed topic thread. Never auto-accepts."""
    check_link_class(channel=turn.channel, thread_class=data_class)
    existing = _active_link(session, turn)
    if existing is not None:
        return existing.id
    nested = session.begin_nested()
    thread = AssistantThread(
        id=uuid.uuid4(),
        title=_proposal_title(turn),
        title_origin="assistant_proposed",
        data_class=data_class,
        status="proposed",
    )
    session.add(thread)
    session.flush()
    link_id: uuid.UUID | None = session.scalar(
        pg_insert(ThreadLink)
        .values(
            id=uuid.uuid4(),
            channel=turn.channel,
            account=turn.account,
            external_thread_id=turn.external_thread_id,
            thread_id=thread.id,
            state="proposed",
        )
        .on_conflict_do_nothing()
        .returning(ThreadLink.id)
    )
    if link_id is None:
        # A concurrent ingest proposed the same mapping first; keep only theirs.
        nested.rollback()
        concurrent = _active_link(session, turn)
        return None if concurrent is None else concurrent.id
    nested.commit()
    return link_id


def ingest_turn(session: Session, payload: Mapping[str, Any] | ChannelTurnV1) -> IngestOutcome:
    """Normalize, gate, and store one inbound turn as ``quarantined``.

    The caller owns the transaction. Rejections and duplicates change no memory
    state; they only append an audit row.
    """
    try:
        turn = parse_turn(payload)
    except ConversationError as error:
        raw = payload if isinstance(payload, Mapping) else None
        return _reject(session, error.code, _safe_identity(raw))
    digest = text_digest(turn.text)
    detail: dict[str, object] = {
        "channel": turn.channel,
        "account": turn.account,
        "role": turn.role,
        "method": turn.ingest_method,
        "text_sha256": digest,
    }

    # 1. Allowlist: unknown or inactive accounts are refused (fail-closed).
    account = _load_account(session, turn.channel, turn.account)
    if account is None:
        return _reject(session, "unknown_account", detail)
    if not account.active:
        return _reject(session, "account_inactive", detail)

    # Data class gate (decision 7.7) before anything is stored.
    data_class = FORCED_CLASS.get(turn.channel, turn.data_class)
    detail["data_class"] = data_class
    if data_class != turn.data_class:
        detail["class_forced"] = True
    if (
        data_class not in CHANNEL_CLASS_CEILING[turn.channel]
        or data_class not in account.allowed_data_classes
    ):
        return _reject(session, "data_class_block", detail)
    if (turn.ingest_method == "deck_ui" and turn.channel != "deck_ui") or (
        turn.channel == "deck_ui" and turn.ingest_method not in ("deck_ui", "owner_typed")
    ):
        return _reject(session, "invalid_method", detail)
    if turn.role == "assistant" and turn.channel in REPLY_CHANNELS:
        # Deck replies go through record_deck_reply, which enforces one reply per message.
        return _reject(session, "reply_path_required", detail)

    # 2. Secret scan: a hit stores nothing but the hash and the reason code.
    if contains_secret(
        turn.text,
        turn.external_thread_id,
        turn.external_message_id,
        turn.in_reply_to,
        turn.source_ref.ref,
    ):
        return _reject(session, "secret_rejected", detail)

    # 5 (read part). Known accepted link maps to its thread; otherwise unmapped.
    thread_id: uuid.UUID | None = None
    link = _active_link(session, turn)
    if link is not None and link.state == "active":
        thread = session.get(AssistantThread, link.thread_id)
        if (
            thread is not None
            and thread.status == "active"
            and class_admits(thread.data_class, data_class)
        ):
            thread_id = thread.id
    in_reply_to = _resolve_in_reply_to(session, turn)
    if turn.in_reply_to is not None and in_reply_to is None:
        detail["in_reply_to_unresolved"] = True

    # 3 + 4. Dedup by unique keys under concurrency; new rows are quarantined.
    message_id = session.scalar(
        pg_insert(AssistantMessage)
        .values(
            id=uuid.uuid4(),
            thread_id=thread_id,
            external_thread_id=turn.external_thread_id,
            external_message_id=turn.external_message_id,
            role=turn.role,
            text=turn.text,
            text_sha256=digest,
            data_class=data_class,
            state="quarantined",
            in_reply_to_message_id=in_reply_to,
            source_date=turn.occurred_at_value(),
            source_channel=turn.channel,
            source_account=turn.account,
            source_ref=turn.source_ref.ref,
            source_ref_sha256=turn.source_ref.sha256,
            method=turn.ingest_method,
            evidence_status=EVIDENCE_DEFAULT,
        )
        .on_conflict_do_nothing()
        .returning(AssistantMessage.id)
    )
    if message_id is None:
        existing = _existing_duplicate(session, turn, digest)
        if existing is not None:
            detail["existing_message_id"] = str(existing)
        record_conversation_event(session, "assistant.turn_duplicate", detail)
        return IngestOutcome(status="duplicate", reason="duplicate", message_id=existing)

    detail["message_id"] = str(message_id)
    link_id: uuid.UUID | None = link.id if link is not None else None
    if thread_id is None:
        detail["thread_unmapped"] = True
        link_id = _propose_link(session, turn, data_class)
    else:
        detail["thread_id"] = str(thread_id)
    if link_id is not None:
        detail["link_id"] = str(link_id)
    record_conversation_event(session, "assistant.turn_accepted", detail)
    return IngestOutcome(
        status="accepted",
        reason="thread_unmapped" if thread_id is None else None,
        message_id=message_id,
        thread_id=thread_id,
        link_id=link_id,
    )


def record_deck_reply(
    session: Session,
    *,
    inbound_message_id: uuid.UUID,
    text: str,
) -> IngestOutcome:
    """Store the Deck's reply to one inbound owner message. It does not send anything.

    Only ``deck_ui`` and ``slack`` get Deck replies. A UNIQUE constraint on
    ``reply_for_message_id`` makes a second reply to the same message a duplicate.
    """
    inbound = session.get(AssistantMessage, inbound_message_id)
    if inbound is None:
        raise ConversationError("reply_target_missing")
    detail: dict[str, object] = {
        "channel": inbound.source_channel,
        "account": inbound.source_account,
        "role": "assistant",
        "reply_for_message_id": str(inbound.id),
    }
    if inbound.source_channel not in REPLY_CHANNELS or inbound.role != "owner":
        return _reject(session, "reply_channel", detail, event="reply_rejected")
    if inbound.state == "rejected":
        return _reject(session, "reply_target_rejected", detail, event="reply_rejected")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        return _reject(session, "invalid_reply", detail, event="reply_rejected")
    digest = text_digest(text)
    detail["text_sha256"] = digest
    if contains_secret(text):
        return _reject(session, "secret_rejected", detail, event="reply_rejected")
    channel = inbound.source_channel
    message_id = session.scalar(
        pg_insert(AssistantMessage)
        .values(
            id=uuid.uuid4(),
            thread_id=inbound.thread_id,
            external_thread_id=inbound.external_thread_id,
            external_message_id=f"deck-reply:{inbound.id}",
            role="assistant",
            text=text,
            text_sha256=digest,
            data_class=inbound.data_class,
            state="quarantined",
            reply_for_message_id=inbound.id,
            in_reply_to_message_id=inbound.id,
            source_date=func.clock_timestamp(),
            source_channel=channel,
            source_account=inbound.source_account,
            source_ref=f"{channel}:reply/{inbound.id}",
            method="deck_ui" if channel == "deck_ui" else "live_push",
            evidence_status=EVIDENCE_DEFAULT,
        )
        .on_conflict_do_nothing()
        .returning(AssistantMessage.id)
    )
    if message_id is None:
        existing = session.scalar(
            select(AssistantMessage.id).where(AssistantMessage.reply_for_message_id == inbound.id)
        )
        if existing is not None:
            detail["existing_message_id"] = str(existing)
        record_conversation_event(session, "assistant.reply_duplicate", detail)
        return IngestOutcome(status="duplicate", reason="duplicate", message_id=existing)
    detail["message_id"] = str(message_id)
    record_conversation_event(session, "assistant.reply_recorded", detail)
    return IngestOutcome(status="accepted", message_id=message_id, thread_id=inbound.thread_id)
