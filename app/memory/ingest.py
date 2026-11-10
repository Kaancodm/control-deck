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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.memory.audit import record_conversation_event
from app.memory.envelope import RESERVED_MESSAGE_PREFIX, TEXT_MAX, ChannelTurnV1, parse_turn
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
from app.privacy.gate import non_local_texts_blocked
from app.privacy.scanner import FindingKind, scan_text

Status = Literal["accepted", "duplicate", "rejected"]
_ACCOUNT = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")


class _LinkTaken(Exception):
    """Another transaction owns the open link. The savepoint rolls the proposal back."""


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


def _secret_reject(
    session: Session, digest: str | None, *, event: str = "turn_rejected"
) -> IngestOutcome:
    """Audit a secret hit with the reason and the text hash only."""
    detail: dict[str, object] = {}
    if digest is not None:
        detail["text_sha256"] = digest
    return _reject(session, "secret_rejected", detail, event=event)


def _text_digest_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return text_digest(value)
    except UnicodeError:
        return None


def _safe_identity(payload: object) -> dict[str, object]:
    """Channel/account for an invalid envelope, only when well-formed and not a secret."""
    if not isinstance(payload, Mapping):
        return {}
    detail: dict[str, object] = {}
    channel = payload.get("channel")
    account = payload.get("account")
    if isinstance(channel, str) and channel in CHANNELS and not contains_secret(channel):
        detail["channel"] = channel
        if (
            isinstance(account, str)
            and _ACCOUNT.fullmatch(account)
            and not contains_secret(account)
        ):
            detail["account"] = account
    digest = _text_digest_or_none(payload.get("text"))
    if digest is not None:
        detail["text_sha256"] = digest
    return detail


def _raw_identity_is_secret(payload: object) -> tuple[str | None, bool]:
    """True when an identity field copied into an audit detail is a secret."""
    if not isinstance(payload, Mapping):
        return None, False
    digest = _text_digest_or_none(payload.get("text"))
    values: list[str] = []
    for key in ("account", "channel"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return digest, contains_secret(*values)


def _load_account(session: Session, channel: str, account: str) -> ChannelAccount | None:
    return session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.channel == channel, ChannelAccount.account_label == account)
        .with_for_update(read=True)
    )


def _load_link(
    session: Session,
    channel: str,
    account: str,
    external_thread_id: str,
    *,
    lock: bool,
) -> ThreadLink | None:
    query = select(ThreadLink).where(
        ThreadLink.channel == channel,
        ThreadLink.account == account,
        ThreadLink.external_thread_id == external_thread_id,
        ThreadLink.state != "rejected",
    )
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def _active_link(session: Session, turn: ChannelTurnV1) -> ThreadLink | None:
    return _load_link(session, turn.channel, turn.account, turn.external_thread_id, lock=False)


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


def _existing_duplicate(
    session: Session, turn: ChannelTurnV1, digest: str
) -> AssistantMessage | None:
    query = select(AssistantMessage).where(
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


def _identity_texts(turn: ChannelTurnV1) -> tuple[str, ...]:
    """Every identity or body string that may be stored or copied into a title."""
    values = [
        turn.account,
        turn.channel,
        turn.external_thread_id,
        turn.source_ref.ref,
        turn.text,
        _proposal_title(turn),
    ]
    if turn.external_message_id is not None:
        values.append(turn.external_message_id)
    if turn.in_reply_to is not None:
        values.append(turn.in_reply_to)
    return tuple(values)


def _audit_body_mismatch(
    session: Session, *, event: str, digest: str, existing_digest: str
) -> None:
    record_conversation_event(
        session,
        event,
        {
            "reason": "duplicate_body_mismatch",
            "text_sha256": digest,
            "existing_text_sha256": existing_digest,
        },
    )


def _propose_link(session: Session, turn: ChannelTurnV1, data_class: str) -> uuid.UUID | None:
    """Store one open mapping proposal with a proposed topic thread. Never auto-accepts."""
    check_link_class(channel=turn.channel, thread_class=data_class)
    existing = _active_link(session, turn)
    if existing is not None:
        return existing.id
    link_id: uuid.UUID | None = None
    try:
        with session.begin_nested():
            thread = AssistantThread(
                id=uuid.uuid4(),
                title=_proposal_title(turn),
                title_origin="assistant_proposed",
                data_class=data_class,
                status="proposed",
            )
            session.add(thread)
            session.flush()
            link_id = session.scalar(
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
                raise _LinkTaken()
    except _LinkTaken:
        concurrent = _active_link(session, turn)
        return None if concurrent is None else concurrent.id
    except IntegrityError:
        concurrent = _active_link(session, turn)
        if concurrent is None:
            raise
        return concurrent.id
    return None if link_id is None else uuid.UUID(str(link_id))


def _active_topic(
    session: Session, link: ThreadLink | None, data_class: str
) -> tuple[uuid.UUID | None, bool]:
    """``(thread_id, blocked)`` for a link already locked by the caller.

    ``blocked`` is true only when the link is active, the topic is active, and
    that topic does not admit ``data_class``. Callers reject a new turn in that
    case and leave an existing row's thread unchanged.
    """
    if link is None or link.state != "active":
        return None, False
    thread = session.scalar(
        select(AssistantThread).where(AssistantThread.id == link.thread_id).with_for_update()
    )
    if thread is None or thread.status != "active":
        return None, False
    if not class_admits(thread.data_class, data_class):
        return None, True
    return thread.id, False


def ingest_turn(session: Session, payload: Mapping[str, Any] | ChannelTurnV1) -> IngestOutcome:
    """Normalize, gate, and store one inbound turn as ``quarantined``.

    The caller owns the transaction. Rejections and duplicates change no memory
    state; they only append an audit row.
    """
    raw = payload if isinstance(payload, Mapping) else None
    try:
        turn = parse_turn(payload)
    except ConversationError as error:
        digest, secret = _raw_identity_is_secret(raw)
        if secret:
            return _secret_reject(session, digest)
        return _reject(session, error.code, _safe_identity(raw))
    digest = text_digest(turn.text)
    # Identity fields and the composed proposal title are scanned before any audit
    # that would copy them, and before the message insert.
    if contains_secret(*_identity_texts(turn)):
        return _secret_reject(session, digest)
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
    if non_local_texts_blocked(data_class, _identity_texts(turn)):
        return _reject(session, "privacy_block", detail)

    # Known accepted link maps to its thread. Lock it before the insert so an
    # accept that commits first is visible, and a later accept sees this row.
    link = _load_link(session, turn.channel, turn.account, turn.external_thread_id, lock=True)
    thread_id, blocked = _active_topic(session, link, data_class)
    if blocked:
        return _reject(session, "thread_class_block", detail)
    in_reply_to = _resolve_in_reply_to(session, turn)
    if turn.in_reply_to is not None and in_reply_to is None:
        detail["in_reply_to_unresolved"] = True

    # Dedup by unique keys under concurrency; new rows are quarantined.
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
        if existing is not None and existing.text_sha256 != digest:
            _audit_body_mismatch(
                session,
                event="assistant.turn_duplicate",
                digest=digest,
                existing_digest=existing.text_sha256,
            )
            return IngestOutcome(
                status="duplicate",
                reason="duplicate_body_mismatch",
                message_id=existing.id,
            )
        if existing is not None:
            detail["existing_message_id"] = str(existing.id)
        record_conversation_event(session, "assistant.turn_duplicate", detail)
        return IngestOutcome(
            status="duplicate",
            reason="duplicate",
            message_id=None if existing is None else existing.id,
        )

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


def _lock_inbound(
    session: Session, message_id: uuid.UUID
) -> tuple[AssistantMessage | None, ThreadLink | None]:
    """Lock the link, then an active topic, then the message (same order as ingest)."""
    preview = session.get(AssistantMessage, message_id)
    if preview is None:
        return None, None
    link = _load_link(
        session,
        preview.source_channel,
        preview.source_account,
        preview.external_thread_id,
        lock=True,
    )
    if link is not None and link.state == "active":
        # Topic before message: ingest_turn locks the topic and then takes
        # FOR KEY SHARE on a referenced message, so the reverse order can cycle.
        session.execute(
            select(AssistantThread.id).where(AssistantThread.id == link.thread_id).with_for_update()
        )
    inbound = session.scalar(
        select(AssistantMessage)
        .where(AssistantMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return inbound, link


def record_deck_reply(
    session: Session,
    *,
    inbound_message_id: uuid.UUID,
    text: str,
) -> IngestOutcome:
    """Store the Deck's reply to one inbound owner message. It does not send anything.

    Only ``deck_ui`` and ``slack`` get Deck replies. A UNIQUE constraint on
    ``reply_for_message_id`` makes a second reply to the same message a duplicate.
    The external id uses :data:`RESERVED_MESSAGE_PREFIX`, which inbound turns cannot mint.
    """
    inbound, link = _lock_inbound(session, inbound_message_id)
    if inbound is None:
        raise ConversationError("reply_target_missing")
    external_message_id = f"{RESERVED_MESSAGE_PREFIX}{inbound.id}"
    source_ref = f"{inbound.source_channel}:reply/{inbound.id}"
    copied = (
        inbound.source_account,
        inbound.source_channel,
        inbound.external_thread_id,
        inbound.source_ref,
        external_message_id,
        source_ref,
        text if isinstance(text, str) else None,
    )
    if contains_secret(*copied):
        return _secret_reject(session, _text_digest_or_none(text), event="reply_rejected")
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
    if non_local_texts_blocked(
        inbound.data_class,
        (text, inbound.source_account, inbound.external_thread_id, source_ref),
    ):
        return _reject(session, "privacy_block", detail, event="reply_rejected")
    mapped, _blocked = _active_topic(session, link, inbound.data_class)
    thread_id = mapped if mapped is not None else inbound.thread_id
    channel = inbound.source_channel
    message_id = session.scalar(
        pg_insert(AssistantMessage)
        .values(
            id=uuid.uuid4(),
            thread_id=thread_id,
            external_thread_id=inbound.external_thread_id,
            external_message_id=external_message_id,
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
            source_ref=source_ref,
            method="deck_ui" if channel == "deck_ui" else "live_push",
            evidence_status=EVIDENCE_DEFAULT,
        )
        .on_conflict_do_nothing()
        .returning(AssistantMessage.id)
    )
    if message_id is None:
        existing = session.scalar(
            select(AssistantMessage).where(AssistantMessage.reply_for_message_id == inbound.id)
        )
        if existing is None:
            existing = session.scalar(
                select(AssistantMessage).where(
                    AssistantMessage.source_channel == channel,
                    AssistantMessage.source_account == inbound.source_account,
                    AssistantMessage.external_message_id == external_message_id,
                )
            )
        if existing is not None and existing.text_sha256 != digest:
            _audit_body_mismatch(
                session,
                event="assistant.reply_duplicate",
                digest=digest,
                existing_digest=existing.text_sha256,
            )
            return IngestOutcome(
                status="duplicate",
                reason="duplicate_body_mismatch",
                message_id=existing.id,
            )
        if existing is not None:
            detail["existing_message_id"] = str(existing.id)
        record_conversation_event(session, "assistant.reply_duplicate", detail)
        return IngestOutcome(
            status="duplicate",
            reason="duplicate",
            message_id=None if existing is None else existing.id,
        )
    detail["message_id"] = str(message_id)
    record_conversation_event(session, "assistant.reply_recorded", detail)
    return IngestOutcome(status="accepted", message_id=message_id, thread_id=thread_id)
