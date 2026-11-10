"""Owner decisions. The only code that moves memory rows out of a pending state.

Every decision needs the digest of an authenticated, unrevoked, unexpired UI session
and the sha256 of the exact bytes the owner saw. A bearer token, a channel, or a
model has neither, so none of them can approve. Nothing is ever deleted; a rejected
row keeps ``rejected`` / ``abgelehnt``.
"""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime
from hashlib import sha256
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.schemas import DataClass
from app.db.ui_auth import UiSession
from app.kb.contracts import allowed_classes
from app.memory.audit import ACTOR_OWNER, record_conversation_event
from app.memory.ingest import contains_secret, text_digest
from app.memory.models import (
    AssistantMessage,
    AssistantThread,
    MemoryApproval,
    MemoryFact,
    ThreadLink,
)
from app.memory.policy import (
    EVIDENCE_DEFAULT,
    ConversationError,
    check_link_class,
)

Decision = Literal["approved", "rejected"]
_HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
_TITLE_MAX = 200
_TRIPLE_MAX = {"subject": 200, "predicate": 200, "object": 1000}
_SENTENCE_MAX = 4000


# Exact bytes the owner is shown. The UI hashes the same bytes before posting a decision.
def message_shown_bytes(message: AssistantMessage) -> bytes:
    return message.text.encode("utf-8")


def fact_shown_bytes(fact: MemoryFact) -> bytes:
    parts = (fact.original_sentence, fact.subject, fact.predicate, fact.object)
    return "\n".join(parts).encode("utf-8")


def thread_shown_bytes(thread: AssistantThread) -> bytes:
    return thread.title.encode("utf-8")


def link_shown_bytes(link: ThreadLink, thread: AssistantThread) -> bytes:
    parts = (link.channel, link.account, link.external_thread_id, str(thread.id), thread.title)
    return "\n".join(parts).encode("utf-8")


def shown_sha256(value: bytes) -> str:
    return sha256(value).hexdigest()


def require_owner_session(session: Session, session_token_hash: str) -> str:
    """Accept only the digest of an authenticated, unrevoked, unexpired UI session."""
    if not isinstance(session_token_hash, str) or not _HEX64.fullmatch(session_token_hash):
        raise ConversationError("owner_session_required")
    found = session.scalar(
        select(UiSession.token_hash)
        .where(
            UiSession.token_hash == session_token_hash,
            UiSession.authenticated.is_(True),
            UiSession.revoked_at.is_(None),
            UiSession.expires_at > func.clock_timestamp(),
        )
        .with_for_update(read=True)
    )
    if found is None:
        raise ConversationError("owner_session_required")
    return session_token_hash


def _check_shown(expected: bytes, provided: str) -> str:
    if not isinstance(provided, str) or not _HEX64.fullmatch(provided):
        raise ConversationError("shown_bytes_mismatch")
    if not hmac.compare_digest(shown_sha256(expected), provided):
        raise ConversationError("shown_bytes_mismatch")
    return provided


def _check_decision(decision: str) -> Decision:
    if decision == "approved":
        return "approved"
    if decision == "rejected":
        return "rejected"
    raise ConversationError("invalid_decision")


def _approval(
    session: Session,
    *,
    kind: str,
    target_id: uuid.UUID,
    decision: Decision,
    shown: str,
    session_token_hash: str,
) -> MemoryApproval:
    if session.scalar(
        select(MemoryApproval.id).where(
            MemoryApproval.kind == kind, MemoryApproval.target_id == target_id
        )
    ):
        raise ConversationError("already_decided")
    row = MemoryApproval(
        id=uuid.uuid4(),
        kind=kind,
        target_id=target_id,
        decision=decision,
        shown_sha256=shown,
        session_token_hash=session_token_hash,
    )
    session.add(row)
    session.flush()
    return row


def _audit(event: str, detail: dict[str, object], session: Session) -> None:
    record_conversation_event(session, f"memory.{event}", detail, actor=ACTOR_OWNER)


def decide_message(
    session: Session,
    message_id: uuid.UUID,
    *,
    decision: str,
    session_token_hash: str,
    shown_sha256_hex: str,
    mark_proven: bool = False,
) -> AssistantMessage:
    """Move a quarantined message to approved or rejected. Only approved rows reach models."""
    checked = _check_decision(decision)
    token = require_owner_session(session, session_token_hash)
    message = session.scalar(
        select(AssistantMessage).where(AssistantMessage.id == message_id).with_for_update()
    )
    if message is None:
        raise ConversationError("not_found")
    if message.state != "quarantined":
        raise ConversationError("already_decided")
    shown = _check_shown(message_shown_bytes(message), shown_sha256_hex)
    approval = _approval(
        session,
        kind="message",
        target_id=message.id,
        decision=checked,
        shown=shown,
        session_token_hash=token,
    )
    message.approval_id = approval.id
    if checked == "approved":
        message.state = "approved"
        message.evidence_status = "bewiesen" if mark_proven else EVIDENCE_DEFAULT
    else:
        message.state = "rejected"
        message.evidence_status = "abgelehnt"
    session.flush()
    _audit(
        f"message_{checked}",
        {
            "message_id": str(message.id),
            "approval_id": str(approval.id),
            "shown_sha256": shown,
            "evidence_status": message.evidence_status,
            "channel": message.source_channel,
            "account": message.source_account,
        },
        session,
    )
    return message


def propose_fact(
    session: Session,
    *,
    source_message_id: uuid.UUID,
    original_sentence: str,
    subject: str,
    predicate: str,
    object_: str,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> MemoryFact:
    """Store a proposed fact. Any caller may propose; only the owner approves."""
    values = {"subject": subject, "predicate": predicate, "object": object_}
    if not isinstance(original_sentence, str) or not (
        0 < len(original_sentence.strip()) and len(original_sentence) <= _SENTENCE_MAX
    ):
        raise ConversationError("invalid_fact")
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip() or len(value) > _TRIPLE_MAX[key]:
            raise ConversationError("invalid_fact")
    for moment in (valid_from, valid_to):
        if moment is not None and moment.tzinfo is None:
            raise ConversationError("invalid_fact")
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        raise ConversationError("invalid_fact")
    source = session.get(AssistantMessage, source_message_id)
    if source is None:
        raise ConversationError("not_found")
    if source.state == "rejected":
        raise ConversationError("source_rejected")
    if contains_secret(original_sentence, subject, predicate, object_):
        _audit_secret(session, original_sentence, source)
        raise ConversationError("secret_rejected")
    fact = MemoryFact(
        id=uuid.uuid4(),
        original_sentence=original_sentence.strip(),
        subject=subject.strip(),
        predicate=predicate.strip(),
        object=object_.strip(),
        source_message_id=source.id,
        data_class=source.data_class,
        valid_from=valid_from,
        valid_to=valid_to,
        status="proposed",
        source_date=source.source_date,
        source_channel=source.source_channel,
        source_account=source.source_account,
        source_ref=source.source_ref,
        source_ref_sha256=source.source_ref_sha256,
        method=source.method,
        evidence_status=EVIDENCE_DEFAULT,
    )
    session.add(fact)
    session.flush()
    record_conversation_event(
        session,
        "memory.fact_proposed",
        {
            "fact_id": str(fact.id),
            "message_id": str(source.id),
            "data_class": fact.data_class,
            "text_sha256": text_digest(fact.original_sentence),
        },
    )
    return fact


def _audit_secret(session: Session, sentence: str, source: AssistantMessage) -> None:
    record_conversation_event(
        session,
        "memory.fact_rejected_input",
        {
            "reason": "secret_rejected",
            "message_id": str(source.id),
            "text_sha256": text_digest(sentence) if isinstance(sentence, str) else None,
        },
    )


def decide_fact(
    session: Session,
    fact_id: uuid.UUID,
    *,
    decision: str,
    session_token_hash: str,
    shown_sha256_hex: str,
    mark_proven: bool = False,
) -> MemoryFact:
    """Owner decides a proposed fact; approved facts may enter model context."""
    checked = _check_decision(decision)
    token = require_owner_session(session, session_token_hash)
    fact = session.scalar(select(MemoryFact).where(MemoryFact.id == fact_id).with_for_update())
    if fact is None:
        raise ConversationError("not_found")
    if fact.status != "proposed":
        raise ConversationError("already_decided")
    shown = _check_shown(fact_shown_bytes(fact), shown_sha256_hex)
    if checked == "approved":
        source = session.get(AssistantMessage, fact.source_message_id)
        if source is None or source.state == "rejected":
            raise ConversationError("source_rejected")
    approval = _approval(
        session,
        kind="fact",
        target_id=fact.id,
        decision=checked,
        shown=shown,
        session_token_hash=token,
    )
    fact.approval_id = approval.id
    if checked == "approved":
        fact.status = "approved"
        fact.evidence_status = "bewiesen" if mark_proven else EVIDENCE_DEFAULT
    else:
        fact.status = "rejected"
        fact.evidence_status = "abgelehnt"
    session.flush()
    _audit(
        f"fact_{checked}",
        {
            "fact_id": str(fact.id),
            "approval_id": str(approval.id),
            "shown_sha256": shown,
            "evidence_status": fact.evidence_status,
        },
        session,
    )
    return fact


def _clean_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > _TITLE_MAX:
        raise ConversationError("invalid_title")
    cleaned = title.strip()
    if contains_secret(cleaned):
        raise ConversationError("secret_rejected")
    return cleaned


def _check_class(value: str) -> str:
    if value not in tuple(item.value for item in DataClass):
        raise ConversationError("invalid_data_class")
    return value


def propose_thread_topic(session: Session, *, title: str, data_class: str) -> AssistantThread:
    """The assistant may propose a topic. It stays ``proposed`` until the owner clicks."""
    thread = AssistantThread(
        id=uuid.uuid4(),
        title=_clean_title(title),
        title_origin="assistant_proposed",
        data_class=_check_class(data_class),
        status="proposed",
    )
    session.add(thread)
    session.flush()
    record_conversation_event(
        session,
        "memory.topic_proposed",
        {"thread_id": str(thread.id), "data_class": thread.data_class},
    )
    return thread


def create_owner_thread(
    session: Session,
    *,
    title: str,
    data_class: str,
    session_token_hash: str,
    shown_sha256_hex: str,
) -> AssistantThread:
    """Owner creates an active topic thread directly; the approval row records the click."""
    token = require_owner_session(session, session_token_hash)
    cleaned = _clean_title(title)
    shown = _check_shown(cleaned.encode("utf-8"), shown_sha256_hex)
    thread_id = uuid.uuid4()
    approval = _approval(
        session,
        kind="thread_topic",
        target_id=thread_id,
        decision="approved",
        shown=shown,
        session_token_hash=token,
    )
    thread = AssistantThread(
        id=thread_id,
        title=cleaned,
        title_origin="owner",
        data_class=_check_class(data_class),
        status="active",
        approval_id=approval.id,
    )
    session.add(thread)
    session.flush()
    _audit(
        "topic_created",
        {"thread_id": str(thread.id), "approval_id": str(approval.id), "shown_sha256": shown},
        session,
    )
    return thread


def decide_thread_topic(
    session: Session,
    thread_id: uuid.UUID,
    *,
    decision: str,
    session_token_hash: str,
    shown_sha256_hex: str,
) -> AssistantThread:
    """Owner accepts or rejects an assistant-proposed topic."""
    checked = _check_decision(decision)
    token = require_owner_session(session, session_token_hash)
    thread = session.scalar(
        select(AssistantThread).where(AssistantThread.id == thread_id).with_for_update()
    )
    if thread is None:
        raise ConversationError("not_found")
    if thread.status != "proposed":
        raise ConversationError("already_decided")
    shown = _check_shown(thread_shown_bytes(thread), shown_sha256_hex)
    approval = _approval(
        session,
        kind="thread_topic",
        target_id=thread.id,
        decision=checked,
        shown=shown,
        session_token_hash=token,
    )
    thread.approval_id = approval.id
    thread.status = "active" if checked == "approved" else "rejected"
    session.flush()
    _audit(
        f"topic_{checked}",
        {"thread_id": str(thread.id), "approval_id": str(approval.id), "shown_sha256": shown},
        session,
    )
    return thread


def decide_thread_link(
    session: Session,
    link_id: uuid.UUID,
    *,
    decision: str,
    session_token_hash: str,
    shown_sha256_hex: str,
    target_thread_id: uuid.UUID | None = None,
) -> ThreadLink:
    """Owner accepts or rejects a mapping proposal, optionally onto another active thread.

    The target thread must already be ``active``; a proposed topic is accepted first
    with :func:`decide_thread_topic`. A local_only thread never links to a cloud channel.
    On acceptance, unmapped messages of that external thread whose class the thread
    admits are attached to it.
    """
    checked = _check_decision(decision)
    token = require_owner_session(session, session_token_hash)
    link = session.scalar(select(ThreadLink).where(ThreadLink.id == link_id).with_for_update())
    if link is None:
        raise ConversationError("not_found")
    if link.state != "proposed":
        raise ConversationError("already_decided")
    chosen = target_thread_id if target_thread_id is not None else link.thread_id
    if checked == "rejected" and chosen != link.thread_id:
        raise ConversationError("invalid_decision")
    thread = session.get(AssistantThread, chosen)
    if thread is None:
        raise ConversationError("not_found")
    shown = _check_shown(link_shown_bytes(link, thread), shown_sha256_hex)
    attached = 0
    if checked == "approved":
        if thread.status != "active":
            raise ConversationError("thread_not_active")
        check_link_class(channel=link.channel, thread_class=thread.data_class)
    approval = _approval(
        session,
        kind="thread_link",
        target_id=link.id,
        decision=checked,
        shown=shown,
        session_token_hash=token,
    )
    link.approval_id = approval.id
    if checked == "approved":
        link.state = "active"
        link.thread_id = thread.id
        admitted = [item.value for item in allowed_classes(DataClass(thread.data_class))]
        result = session.execute(
            update(AssistantMessage)
            .where(
                AssistantMessage.source_channel == link.channel,
                AssistantMessage.source_account == link.account,
                AssistantMessage.external_thread_id == link.external_thread_id,
                AssistantMessage.thread_id.is_(None),
                AssistantMessage.data_class.in_(admitted),
            )
            .values(thread_id=thread.id)
            .execution_options(synchronize_session=False)
        )
        attached = int(getattr(result, "rowcount", 0) or 0)
    else:
        link.state = "rejected"
    session.flush()
    _audit(
        f"link_{checked}",
        {
            "link_id": str(link.id),
            "thread_id": str(thread.id),
            "approval_id": str(approval.id),
            "shown_sha256": shown,
            "channel": link.channel,
            "account": link.account,
            "attached": attached,
        },
        session,
    )
    return link


__all__ = [
    "create_owner_thread",
    "decide_fact",
    "decide_message",
    "decide_thread_link",
    "decide_thread_topic",
    "fact_shown_bytes",
    "link_shown_bytes",
    "message_shown_bytes",
    "propose_fact",
    "propose_thread_topic",
    "require_owner_session",
    "shown_sha256",
    "thread_shown_bytes",
]
