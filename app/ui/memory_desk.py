"""Quarantine inbox and correction cards. Hashes use the approval shown bytes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.memory.approval import (
    fact_shown_bytes,
    link_shown_bytes,
    message_shown_bytes,
    shown_sha256,
    thread_shown_bytes,
)
from app.memory.models import AssistantMessage, AssistantThread, MemoryFact, ThreadLink
from app.ui.memory_labels import HOLD_LABELS, STATE_LABELS

_LIMIT = 30


@dataclass(frozen=True)
class DeskAction:
    """One click. The digest is sha256 of ``shown``, which the page renders."""

    label: str
    kind: str
    action: str
    target: str
    decision: str
    mark_proven: bool
    shown: str
    digest: str
    clear_valid_to: bool = False
    clear_superseded_by: bool = False


@dataclass(frozen=True)
class DeskCard:
    anchor: str
    heading: str
    text: str
    chips: tuple[tuple[str, str], ...]
    hold_code: str | None
    hold_label: str | None
    actions: tuple[DeskAction, ...]
    propose: bool
    bounds: bool
    source_id: str
    valid_to: str
    superseded_by: str


@dataclass(frozen=True)
class MemoryDesk:
    quarantine: tuple[DeskCard, ...]
    facts: tuple[DeskCard, ...]
    topics: tuple[DeskCard, ...]
    links: tuple[DeskCard, ...]
    corrections: tuple[DeskCard, ...]
    quarantine_more: bool = False
    facts_more: bool = False
    topics_more: bool = False
    links_more: bool = False


def _open_rows[RowT](session: Session, statement: Select[RowT]) -> tuple[list[RowT], bool]:
    """Filter is already in ``statement``. Read one past the cap so the page can say more exist."""
    found = list(session.scalars(statement.limit(_LIMIT + 1)))
    return found[:_LIMIT], len(found) > _LIMIT


def load_desk(session: Session) -> MemoryDesk:
    """Read the inbox. Open rows are filtered before the cap. This function does not write."""
    messages, quarantine_more = _open_rows(
        session,
        select(AssistantMessage)
        .where(AssistantMessage.state == "quarantined")
        .order_by(AssistantMessage.seq.desc()),
    )
    proposed_fact_rows, facts_more = _open_rows(
        session,
        select(MemoryFact)
        .where(MemoryFact.status == "proposed")
        .order_by(MemoryFact.source_date.desc()),
    )
    topic_rows, topics_more = _open_rows(
        session,
        select(AssistantThread)
        .where(AssistantThread.status == "proposed")
        .order_by(AssistantThread.created_at.desc()),
    )
    link_rows, links_more = _open_rows(
        session,
        select(ThreadLink)
        .where(ThreadLink.state == "proposed")
        .order_by(ThreadLink.created_at.desc()),
    )
    decided_messages = list(
        session.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.state.in_(("approved", "rejected")))
            .order_by(AssistantMessage.seq.desc())
            .limit(_LIMIT)
        )
    )
    decided_facts = list(
        session.scalars(
            select(MemoryFact)
            .where(MemoryFact.status.in_(("approved", "rejected")))
            .order_by(MemoryFact.source_date.desc())
            .limit(_LIMIT)
        )
    )
    decided_topics = list(
        session.scalars(
            select(AssistantThread)
            .where(AssistantThread.status.in_(("active", "rejected")))
            .order_by(AssistantThread.created_at.desc())
            .limit(_LIMIT)
        )
    )
    decided_links = list(
        session.scalars(
            select(ThreadLink)
            .where(ThreadLink.state.in_(("active", "rejected")))
            .order_by(ThreadLink.created_at.desc())
            .limit(_LIMIT)
        )
    )
    thread_ids = {row.thread_id for row in (*link_rows, *decided_links)}
    threads = (
        {
            row.id: row
            for row in session.scalars(
                select(AssistantThread).where(AssistantThread.id.in_(thread_ids))
            )
        }
        if thread_ids
        else {}
    )
    proposed_links = tuple(
        card
        for row in link_rows
        if (card := _link_card(row, threads.get(row.thread_id))) is not None
    )
    corrections = (
        tuple(_message_card(row) for row in decided_messages)
        + tuple(_fact_card(row) for row in decided_facts)
        + tuple(_thread_card(row) for row in decided_topics)
        + tuple(
            card
            for row in decided_links
            if (card := _link_card(row, threads.get(row.thread_id))) is not None
        )
    )
    return MemoryDesk(
        quarantine=tuple(_message_card(row) for row in messages),
        facts=tuple(_fact_card(row) for row in proposed_fact_rows),
        topics=tuple(_thread_card(row) for row in topic_rows),
        links=proposed_links,
        corrections=corrections,
        quarantine_more=quarantine_more,
        facts_more=facts_more,
        topics_more=topics_more,
        links_more=links_more,
    )


def _action(
    *,
    label: str,
    kind: str,
    action: str,
    target: uuid.UUID,
    decision: str,
    mark_proven: bool,
    payload: bytes,
    clear_valid_to: bool = False,
    clear_superseded_by: bool = False,
) -> DeskAction:
    return DeskAction(
        label=label,
        kind=kind,
        action=action,
        target=str(target),
        decision=decision,
        mark_proven=mark_proven,
        shown=payload.decode("utf-8"),
        digest=shown_sha256(payload),
        clear_valid_to=clear_valid_to,
        clear_superseded_by=clear_superseded_by,
    )


def _hold(code: str | None) -> str | None:
    if code is None:
        return None
    return HOLD_LABELS.get(code)


def _message_card(message: AssistantMessage) -> DeskCard:
    actions: list[DeskAction] = []
    if message.state == "quarantined":
        actions.extend(
            (
                _message_action(message, "Freigeben", "decide", "approved", False),
                _message_action(message, "Als bewiesen freigeben", "decide", "approved", True),
                _message_action(message, "Ablehnen", "decide", "rejected", False),
            )
        )
    elif message.state == "approved" and message.evidence_status == "bewiesen":
        actions.extend(
            (
                _message_action(message, "Nachweis zurücknehmen", "correct", "approved", False),
                _message_action(message, "Auf abgelehnt korrigieren", "correct", "rejected", False),
            )
        )
    elif message.state == "approved":
        actions.extend(
            (
                _message_action(message, "Als bewiesen korrigieren", "correct", "approved", True),
                _message_action(message, "Auf abgelehnt korrigieren", "correct", "rejected", False),
            )
        )
    else:
        actions.extend(
            (
                _message_action(
                    message, "Auf freigegeben korrigieren", "correct", "approved", False
                ),
                _message_action(message, "Auf bewiesen korrigieren", "correct", "approved", True),
            )
        )
    return DeskCard(
        anchor=f"msg-{message.id}",
        heading=f"Nachricht · {STATE_LABELS.get(message.state, message.state)}",
        text=message.text,
        chips=(
            ("Kanal", message.source_channel),
            ("Konto", message.source_account),
            ("Methode", message.method),
            ("Herkunft", message.source_ref),
            ("Nachweis", message.evidence_status),
            ("Rolle", message.role),
            ("Klasse", message.data_class),
        ),
        hold_code=message.privacy_hold,
        hold_label=_hold(message.privacy_hold),
        actions=tuple(actions),
        propose=message.state != "rejected",
        bounds=False,
        source_id=str(message.id),
        valid_to="",
        superseded_by="",
    )


def _message_action(
    message: AssistantMessage, label: str, action: str, decision: str, mark_proven: bool
) -> DeskAction:
    corrects = message.approval_id if action == "correct" else None
    return _action(
        label=label,
        kind="message",
        action=action,
        target=message.id,
        decision=decision,
        mark_proven=mark_proven,
        payload=message_shown_bytes(
            message, decision=decision, mark_proven=mark_proven, corrects=corrects
        ),
    )


def _fact_card(fact: MemoryFact) -> DeskCard:
    actions: list[DeskAction] = []
    if fact.status == "proposed":
        actions.extend(
            (
                _fact_decision(fact, "Freigeben", "decide", "approved", False),
                _fact_decision(fact, "Als bewiesen freigeben", "decide", "approved", True),
                _fact_decision(fact, "Ablehnen", "decide", "rejected", False),
            )
        )
    else:
        actions.extend(_fact_corrections(fact))
        actions.extend(_fact_clears(fact))
    return DeskCard(
        anchor=f"fact-{fact.id}",
        heading=f"Faktum · {STATE_LABELS.get(fact.status, fact.status)}",
        text="\n".join((fact.original_sentence, fact.subject, fact.predicate, fact.object)),
        chips=(
            ("Kanal", fact.source_channel),
            ("Konto", fact.source_account),
            ("Methode", fact.method),
            ("Herkunft", fact.source_ref),
            ("Nachweis", fact.evidence_status),
            ("Klasse", fact.data_class),
        ),
        hold_code=fact.privacy_hold,
        hold_label=_hold(fact.privacy_hold),
        actions=tuple(actions),
        propose=False,
        bounds=fact.status in {"approved", "rejected"},
        source_id=str(fact.id),
        valid_to="" if fact.valid_to is None else fact.valid_to.isoformat(),
        superseded_by="" if fact.superseded_by is None else str(fact.superseded_by),
    )


def _fact_decision(
    fact: MemoryFact, label: str, action: str, decision: str, mark_proven: bool
) -> DeskAction:
    corrects = fact.approval_id if action == "correct" else None
    return _action(
        label=label,
        kind="fact",
        action=action,
        target=fact.id,
        decision=decision,
        mark_proven=mark_proven,
        payload=fact_shown_bytes(
            fact, decision=decision, mark_proven=mark_proven, corrects=corrects
        ),
    )


def _fact_corrections(fact: MemoryFact) -> tuple[DeskAction, ...]:
    if fact.status == "approved" and fact.evidence_status == "bewiesen":
        choices = (
            ("Nachweis zurücknehmen", "approved", False),
            ("Auf abgelehnt korrigieren", "rejected", False),
        )
    elif fact.status == "approved":
        choices = (
            ("Als bewiesen korrigieren", "approved", True),
            ("Auf abgelehnt korrigieren", "rejected", False),
        )
    else:
        choices = (
            ("Auf freigegeben korrigieren", "approved", False),
            ("Auf bewiesen korrigieren", "approved", True),
        )
    return tuple(
        _fact_decision(fact, label, "correct", decision, proven)
        for label, decision, proven in choices
    )


def _fact_clears(fact: MemoryFact) -> tuple[DeskAction, ...]:
    """Clear is its own correction. It does not change the stored decision."""
    if fact.approval_id is None:
        return ()
    decision = "approved" if fact.status == "approved" else "rejected"
    proven = fact.evidence_status == "bewiesen"
    actions: list[DeskAction] = []
    if fact.valid_to is not None:
        actions.append(
            _clear_action(fact, "valid_to leeren", decision, proven, clear_valid_to=True)
        )
    if fact.superseded_by is not None:
        actions.append(
            _clear_action(fact, "Nachfolger leeren", decision, proven, clear_superseded_by=True)
        )
    if fact.valid_to is not None and fact.superseded_by is not None:
        actions.append(
            _clear_action(
                fact,
                "Beide Grenzen leeren",
                decision,
                proven,
                clear_valid_to=True,
                clear_superseded_by=True,
            )
        )
    return tuple(actions)


def _clear_action(
    fact: MemoryFact,
    label: str,
    decision: str,
    mark_proven: bool,
    *,
    clear_valid_to: bool = False,
    clear_superseded_by: bool = False,
) -> DeskAction:
    return _action(
        label=label,
        kind="fact",
        action="clear",
        target=fact.id,
        decision=decision,
        mark_proven=mark_proven,
        payload=fact_shown_bytes(
            fact,
            decision=decision,
            mark_proven=mark_proven,
            corrects=fact.approval_id,
            clear_valid_to=clear_valid_to,
            clear_superseded_by=clear_superseded_by,
        ),
        clear_valid_to=clear_valid_to,
        clear_superseded_by=clear_superseded_by,
    )


def _thread_card(thread: AssistantThread) -> DeskCard:
    actions: tuple[DeskAction, ...]
    if thread.status == "proposed":
        actions = (
            _thread_action(thread, "Thema freigeben", "decide", "approved"),
            _thread_action(thread, "Thema ablehnen", "decide", "rejected"),
        )
    elif thread.status == "active":
        actions = (_thread_action(thread, "Thema ablehnen", "correct", "rejected"),)
    else:
        actions = (_thread_action(thread, "Thema wieder freigeben", "correct", "approved"),)
    return DeskCard(
        anchor=f"topic-{thread.id}",
        heading=f"Thema · {STATE_LABELS.get(thread.status, thread.status)}",
        text=thread.title,
        chips=(("Klasse", thread.data_class), ("Herkunft", thread.title_origin)),
        hold_code=None,
        hold_label=None,
        actions=actions,
        propose=False,
        bounds=False,
        source_id=str(thread.id),
        valid_to="",
        superseded_by="",
    )


def _thread_action(thread: AssistantThread, label: str, action: str, decision: str) -> DeskAction:
    corrects = thread.approval_id if action == "correct" else None
    return _action(
        label=label,
        kind="thread",
        action=action,
        target=thread.id,
        decision=decision,
        mark_proven=False,
        payload=thread_shown_bytes(thread, decision=decision, corrects=corrects),
    )


def _link_card(link: ThreadLink, thread: AssistantThread | None) -> DeskCard | None:
    if thread is None:
        return None
    actions: tuple[DeskAction, ...]
    if link.state == "proposed":
        actions = (
            _link_action(link, thread, "Verknüpfung freigeben", "decide", "approved"),
            _link_action(link, thread, "Verknüpfung ablehnen", "decide", "rejected"),
        )
    elif link.state == "active":
        actions = (_link_action(link, thread, "Verknüpfung lösen", "correct", "rejected"),)
    else:
        actions = (
            _link_action(link, thread, "Verknüpfung wieder freigeben", "correct", "approved"),
        )
    return DeskCard(
        anchor=f"link-{link.id}",
        heading=f"Verknüpfung · {STATE_LABELS.get(link.state, link.state)}",
        text=f"{link.external_thread_id}\n{thread.title}",
        chips=(
            ("Kanal", link.channel),
            ("Konto", link.account),
            ("Faden", link.external_thread_id),
            ("Thema", thread.title),
            ("Klasse", thread.data_class),
        ),
        hold_code=None,
        hold_label=None,
        actions=actions,
        propose=False,
        bounds=False,
        source_id=str(link.id),
        valid_to="",
        superseded_by="",
    )


def _link_action(
    link: ThreadLink, thread: AssistantThread, label: str, action: str, decision: str
) -> DeskAction:
    corrects = link.approval_id if action == "correct" else None
    return _action(
        label=label,
        kind="link",
        action=action,
        target=link.id,
        decision=decision,
        mark_proven=False,
        payload=link_shown_bytes(link, thread, decision=decision, corrects=corrects),
    )
