"""Owner decisions. The only code that moves memory rows out of a pending state.

Every decision takes the raw UI session cookie. The digest is derived with
``token_digest`` inside ``get_ui_session`` (owner, idle timeout, password
fingerprint). The approval row stores a non-reversible approver reference, not
the session token hash. A bearer token, a channel, or a model cannot approve.
Nothing is ever deleted; a rejected row keeps ``rejected`` / ``abgelehnt``.
"""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime
from hashlib import sha256
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.schemas import DataClass
from app.db.ui_auth import get_ui_session
from app.kb.contracts import allowed_classes
from app.memory.audit import ACTOR_OWNER, record_conversation_event
from app.memory.context import render_fact_text
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
from app.privacy.gate import non_local_texts_blocked

Decision = Literal["approved", "rejected"]
_HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
_TITLE_MAX = 200
_TRIPLE_MAX = {"subject": 200, "predicate": 200, "object": 1000}
_SENTENCE_MAX = 4000
_SHOWN_VERSION = "memory-shown.v1"
# Domain separation so the stored reference is not the session token hash.
_APPROVER_PREFIX = b"deck-memory-approver\0"


def _canonical_shown(fields: tuple[tuple[str, str], ...], body: str) -> bytes:
    """Versioned UTF-8 payload. A blank line separates headers from the text."""
    header = "\n".join((_SHOWN_VERSION, *(f"{key}={value}" for key, value in fields)))
    return f"{header}\n\n{body}".encode()


def _flag(mark_proven: bool) -> str:
    return "1" if mark_proven else "0"


# Exact bytes the owner is shown. The UI hashes the same bytes before posting a decision.
def message_shown_bytes(
    message: AssistantMessage, *, decision: str, mark_proven: bool = False
) -> bytes:
    return _canonical_shown(
        (
            ("kind", "message"),
            ("data_class", message.data_class),
            ("channel", message.source_channel),
            ("account", message.source_account),
            ("role", message.role),
            ("decision", decision),
            ("mark_proven", _flag(mark_proven)),
        ),
        message.text,
    )


def fact_shown_bytes(fact: MemoryFact, *, decision: str, mark_proven: bool = False) -> bytes:
    body = "\n".join((fact.original_sentence, fact.subject, fact.predicate, fact.object))
    return _canonical_shown(
        (
            ("kind", "fact"),
            ("data_class", fact.data_class),
            ("channel", fact.source_channel),
            ("account", fact.source_account),
            ("decision", decision),
            ("mark_proven", _flag(mark_proven)),
        ),
        body,
    )


def thread_shown_bytes(
    thread: AssistantThread | None = None,
    *,
    title: str | None = None,
    data_class: str | None = None,
    decision: str,
    mark_proven: bool = False,
) -> bytes:
    resolved_title = thread.title if thread is not None else title
    resolved_class = thread.data_class if thread is not None else data_class
    if resolved_title is None or resolved_class is None:
        raise ConversationError("invalid_title")
    return _canonical_shown(
        (
            ("kind", "thread"),
            ("data_class", resolved_class),
            ("decision", decision),
            ("mark_proven", _flag(mark_proven)),
        ),
        resolved_title,
    )


def link_shown_bytes(
    link: ThreadLink,
    thread: AssistantThread,
    *,
    decision: str,
    mark_proven: bool = False,
) -> bytes:
    body = f"{link.external_thread_id}\n{thread.title}"
    return _canonical_shown(
        (
            ("kind", "thread_link"),
            ("data_class", thread.data_class),
            ("channel", link.channel),
            ("account", link.account),
            ("thread_id", str(thread.id)),
            ("decision", decision),
            ("mark_proven", _flag(mark_proven)),
        ),
        body,
    )


def shown_sha256(value: bytes) -> str:
    return sha256(value).hexdigest()


def approver_reference(token_hash: str) -> str:
    """Non-reversible reference. It is not a session lookup key."""
    return sha256(_APPROVER_PREFIX + token_hash.encode("ascii")).hexdigest()


def require_owner_session(session: Session, session_token: str) -> str:
    """Validate the raw cookie and return the approver reference, not the token hash."""
    row = get_ui_session(session, session_token, owner_required=True, for_update=True)
    if row is None:
        raise ConversationError("owner_session_required")
    return approver_reference(row.token_hash)


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
    approver_ref: str,
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
        approver_ref=approver_ref,
    )
    session.add(row)
    session.flush()
    return row


def _audit(event: str, detail: dict[str, object], session: Session) -> None:
    record_conversation_event(session, f"memory.{event}", detail, actor=ACTOR_OWNER)


def _visible_identity(channel: str, account: str) -> dict[str, object]:
    """Copy channel and account into an audit detail only when neither is a secret."""
    detail: dict[str, object] = {}
    if not contains_secret(channel):
        detail["channel"] = channel
    if not contains_secret(account):
        detail["account"] = account
    return detail


def decide_message(
    session: Session,
    message_id: uuid.UUID,
    *,
    decision: str,
    session_token: str,
    shown_sha256_hex: str,
    mark_proven: bool = False,
) -> AssistantMessage:
    """Move a quarantined message to approved or rejected. Only approved rows reach models."""
    checked = _check_decision(decision)
    approver_ref = require_owner_session(session, session_token)
    message = session.scalar(
        select(AssistantMessage).where(AssistantMessage.id == message_id).with_for_update()
    )
    if message is None:
        raise ConversationError("not_found")
    if message.state != "quarantined":
        raise ConversationError("already_decided")
    shown = _check_shown(
        message_shown_bytes(message, decision=checked, mark_proven=mark_proven),
        shown_sha256_hex,
    )
    approval = _approval(
        session,
        kind="message",
        target_id=message.id,
        decision=checked,
        shown=shown,
        approver_ref=approver_ref,
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
            **_visible_identity(message.source_channel, message.source_account),
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
    source = session.scalar(
        select(AssistantMessage)
        .where(AssistantMessage.id == source_message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise ConversationError("not_found")
    if source.state == "rejected":
        raise ConversationError("source_rejected")
    sentence = original_sentence.strip()
    subject_text = subject.strip()
    predicate_text = predicate.strip()
    object_text = object_.strip()
    joined = "\n".join((sentence, subject_text, predicate_text, object_text))
    rendered = render_fact_text(
        subject=subject_text,
        predicate=predicate_text,
        object_=object_text,
        sentence=sentence,
    )
    if contains_secret(sentence, subject_text, predicate_text, object_text, joined, rendered):
        _audit_secret(session, sentence, source)
        raise ConversationError("secret_rejected")
    if non_local_texts_blocked(
        source.data_class,
        (sentence, subject_text, predicate_text, object_text, joined, rendered),
    ):
        record_conversation_event(
            session,
            "memory.fact_rejected_input",
            {
                "reason": "privacy_block",
                "message_id": str(source.id),
                "text_sha256": text_digest(joined),
            },
        )
        raise ConversationError("privacy_block")
    fact = MemoryFact(
        id=uuid.uuid4(),
        original_sentence=sentence,
        subject=subject_text,
        predicate=predicate_text,
        object=object_text,
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
    session_token: str,
    shown_sha256_hex: str,
    mark_proven: bool = False,
) -> MemoryFact:
    """Owner decides a proposed fact; approved facts may enter model context."""
    checked = _check_decision(decision)
    approver_ref = require_owner_session(session, session_token)
    fact = session.scalar(select(MemoryFact).where(MemoryFact.id == fact_id).with_for_update())
    if fact is None:
        raise ConversationError("not_found")
    if fact.status != "proposed":
        raise ConversationError("already_decided")
    shown = _check_shown(
        fact_shown_bytes(fact, decision=checked, mark_proven=mark_proven),
        shown_sha256_hex,
    )
    if checked == "approved":
        source = session.scalar(
            select(AssistantMessage)
            .where(AssistantMessage.id == fact.source_message_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if source is None or source.state == "rejected":
            raise ConversationError("source_rejected")
    approval = _approval(
        session,
        kind="fact",
        target_id=fact.id,
        decision=checked,
        shown=shown,
        approver_ref=approver_ref,
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
    session_token: str,
    shown_sha256_hex: str,
) -> AssistantThread:
    """Owner creates an active topic thread directly; the approval row records the click."""
    approver_ref = require_owner_session(session, session_token)
    cleaned = _clean_title(title)
    checked_class = _check_class(data_class)
    shown = _check_shown(
        thread_shown_bytes(title=cleaned, data_class=checked_class, decision="approved"),
        shown_sha256_hex,
    )
    thread_id = uuid.uuid4()
    approval = _approval(
        session,
        kind="thread_topic",
        target_id=thread_id,
        decision="approved",
        shown=shown,
        approver_ref=approver_ref,
    )
    thread = AssistantThread(
        id=thread_id,
        title=cleaned,
        title_origin="owner",
        data_class=checked_class,
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
    session_token: str,
    shown_sha256_hex: str,
) -> AssistantThread:
    """Owner accepts or rejects an assistant-proposed topic."""
    checked = _check_decision(decision)
    approver_ref = require_owner_session(session, session_token)
    thread = session.scalar(
        select(AssistantThread).where(AssistantThread.id == thread_id).with_for_update()
    )
    if thread is None:
        raise ConversationError("not_found")
    if thread.status != "proposed":
        raise ConversationError("already_decided")
    shown = _check_shown(thread_shown_bytes(thread, decision=checked), shown_sha256_hex)
    approval = _approval(
        session,
        kind="thread_topic",
        target_id=thread.id,
        decision=checked,
        shown=shown,
        approver_ref=approver_ref,
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
    session_token: str,
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
    approver_ref = require_owner_session(session, session_token)
    link = session.scalar(select(ThreadLink).where(ThreadLink.id == link_id).with_for_update())
    if link is None:
        raise ConversationError("not_found")
    if link.state != "proposed":
        raise ConversationError("already_decided")
    chosen = target_thread_id if target_thread_id is not None else link.thread_id
    if checked == "rejected" and chosen != link.thread_id:
        raise ConversationError("invalid_decision")
    # Lock and refresh the topic so its active state and class cannot change
    # before messages attach; same link -> topic order as ingest.
    thread = session.scalar(
        select(AssistantThread)
        .where(AssistantThread.id == chosen)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if thread is None:
        raise ConversationError("not_found")
    shown = _check_shown(link_shown_bytes(link, thread, decision=checked), shown_sha256_hex)
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
        approver_ref=approver_ref,
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
            .execution_options(synchronize_session="fetch")
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
            "attached": attached,
            **_visible_identity(link.channel, link.account),
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
    "approver_reference",
    "require_owner_session",
    "shown_sha256",
    "thread_shown_bytes",
]
