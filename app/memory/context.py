"""Approved memory for a model prompt (decision 7.3). Read-only; sends nothing.

Order of operations:
1. target filter: codex gets nothing (no read at all); ollama may get every class;
   claude, claude_code and grok get only ``cloud_ok``. ``cloud_redacted`` is excluded
   for cloud targets because ``app/redaction`` is bound to a task and its
   ``redaction_maps``; conversation memory has no task, so no redaction applies here.
2. class filter via ``allowed_classes`` in SQL, BEFORE any ranking or text handling.
3. only ``approved`` rows; quarantined, proposed and rejected rows never appear.
   For cloud targets, rows with a ``privacy_hold`` (and facts whose source message
   has one) are excluded in SQL as well, even after approval. ``ollama`` gets them.
4. budget: profile, then thread history (newest kept, oldest dropped first), then
   facts (exact-thread first, then simple keyword hits). Same token estimate as
   ``compose_dialogue_messages``. No embeddings. ``num_ctx`` is not changed here.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.assistant.dialogue import current_profile, estimate_dialogue_tokens
from app.core.schemas import DataClass
from app.kb.contracts import allowed_classes
from app.memory.ingest import contains_secret
from app.memory.models import AssistantMessage, AssistantThread, MemoryFact
from app.memory.policy import CLOUD_TARGETS, TARGETS, ConversationError
from app.privacy.gate import non_local_texts_blocked

# Mirrors the dialogue defaults (num_ctx 4096, num_predict 400, template reserve 64);
# half of the remaining room is left for the system rules and the current message.
DEFAULT_BUDGET_TOKENS = (4096 - 400 - 64) // 2
_HISTORY_LIMIT = 200
_FACT_CANDIDATES = 500
_WORD = re.compile(r"\w{3,}", re.UNICODE)

ItemKind = Literal["profile", "history", "fact"]


@dataclass(frozen=True)
class MemoryItem:
    kind: ItemKind
    reference: str
    data_class: str
    role: str | None
    # ``bewiesen``, ``unbewiesen``, ``abgelehnt``, or ``owner-typed`` for the profile.
    evidence_status: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class MemoryContext:
    target: str
    classes: tuple[str, ...]
    items: tuple[MemoryItem, ...] = ()
    dropped: int = 0
    tokens: int = 0


def target_classes(target: str) -> tuple[str, ...]:
    """Classes a target may ever receive, independent of the turn's own class."""
    if target == "codex":
        return ()
    if target == "ollama":
        return tuple(item.value for item in DataClass)
    if target in CLOUD_TARGETS:
        return (DataClass.CLOUD_OK.value,)
    raise ConversationError("unknown_target")


def context_classes(*, target: str, data_class: str) -> tuple[str, ...]:
    """Intersection of the turn's allowed classes and the target ceiling."""
    if target not in TARGETS:
        raise ConversationError("unknown_target")
    try:
        turn_class = DataClass(data_class)
    except ValueError:
        raise ConversationError("invalid_data_class") from None
    if target in CLOUD_TARGETS and turn_class == DataClass.LOCAL_ONLY:
        raise ConversationError("data_class_block")
    ceiling = target_classes(target)
    return tuple(item.value for item in allowed_classes(turn_class) if item.value in ceiling)


def _keywords(query: str | None) -> frozenset[str]:
    if not query:
        return frozenset()
    return frozenset(word.lower() for word in _WORD.findall(query))


def render_fact_text(*, subject: str, predicate: str, object_: str, sentence: str) -> str:
    """The one fact string shown in context and scanned at proposal time."""
    return f"{subject} | {predicate} | {object_} ({sentence})"


def _fact_text(fact: MemoryFact) -> str:
    return render_fact_text(
        subject=fact.subject,
        predicate=fact.predicate,
        object_=fact.object,
        sentence=fact.original_sentence,
    )


def load_memory_context(
    session: Session,
    *,
    thread_id: uuid.UUID | None,
    target: str,
    data_class: str,
    query: str | None = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> MemoryContext:
    """Return approved, class-filtered memory that fits ``budget_tokens``."""
    classes = context_classes(target=target, data_class=data_class)
    if not classes:
        # Codex (or an empty intersection): no database read at all.
        return MemoryContext(target=target, classes=())
    if budget_tokens < 1:
        raise ConversationError("invalid_budget")

    candidates: list[MemoryItem] = []
    # Profile notes carry no class; they are treated as local_only.
    profile: MemoryItem | None = None
    if DataClass.LOCAL_ONLY.value in classes:
        row = current_profile(session)
        if row is not None and row.notes.strip():
            profile = MemoryItem(
                "profile",
                f"profile:{row.version}",
                "local_only",
                None,
                "owner-typed",
                row.notes.strip(),
            )

    # Rows with a privacy hold never reach a cloud target, even after approval.
    cloud = target in CLOUD_TARGETS
    history: list[MemoryItem] = []
    if thread_id is not None:
        thread = session.get(AssistantThread, thread_id)
        if thread is not None and thread.status == "active" and thread.data_class in classes:
            history_filter: list[ColumnElement[bool]] = [
                AssistantMessage.thread_id == thread_id,
                AssistantMessage.state == "approved",
                AssistantMessage.data_class.in_(classes),
            ]
            if cloud:
                history_filter.append(AssistantMessage.privacy_hold.is_(None))
            rows = session.scalars(
                select(AssistantMessage)
                .where(*history_filter)
                .order_by(AssistantMessage.seq.desc())
                .limit(_HISTORY_LIMIT)
            )
            history = [
                MemoryItem(
                    "history",
                    str(row.id),
                    row.data_class,
                    row.role,
                    row.evidence_status,
                    row.text,
                )
                for row in rows
            ]  # newest first

    words = _keywords(query)
    facts: list[MemoryItem] = []
    if thread_id is not None or words:
        now = func.clock_timestamp()
        fact_filter: list[ColumnElement[bool]] = [
            MemoryFact.status == "approved",
            MemoryFact.superseded_by.is_(None),
            MemoryFact.data_class.in_(classes),
            or_(MemoryFact.valid_to.is_(None), MemoryFact.valid_to > now),
            AssistantMessage.state != "rejected",
        ]
        if cloud:
            fact_filter.append(MemoryFact.privacy_hold.is_(None))
            fact_filter.append(AssistantMessage.privacy_hold.is_(None))
        statement = (
            select(MemoryFact, AssistantMessage.thread_id)
            .join(AssistantMessage, AssistantMessage.id == MemoryFact.source_message_id)
            .where(*fact_filter)
            .order_by(MemoryFact.created_at.desc(), MemoryFact.id)
            .limit(_FACT_CANDIDATES)
        )
        ranked: list[tuple[int, int, int, MemoryFact]] = []
        for position, (fact, source_thread) in enumerate(session.execute(statement).all()):
            in_thread = 1 if thread_id is not None and source_thread == thread_id else 0
            haystack = " ".join(
                (fact.original_sentence, fact.subject, fact.predicate, fact.object)
            ).lower()
            hits = sum(1 for word in words if word in haystack)
            if in_thread or hits:
                ranked.append((-in_thread, -hits, position, fact))
        ranked.sort(key=lambda entry: entry[:3])
        facts = [
            MemoryItem(
                "fact", str(fact.id), fact.data_class, None, fact.evidence_status, _fact_text(fact)
            )
            for *_, fact in ranked
        ]

    used = 0
    dropped = 0

    def fits(item: MemoryItem) -> bool:
        nonlocal used, dropped
        if contains_secret(item.text) or (
            target != "ollama" and non_local_texts_blocked(item.data_class, (item.text,))
        ):
            dropped += 1
            return False
        cost = estimate_dialogue_tokens(item.text)
        if used + cost > budget_tokens:
            dropped += 1
            return False
        used += cost
        return True

    if profile is not None and fits(profile):
        candidates.append(profile)
    kept_history: list[MemoryItem] = []
    for index, item in enumerate(history):
        if not fits(item):
            # Everything older than the first item that no longer fits is dropped too.
            dropped += len(history) - index - 1
            break
        kept_history.append(item)
    candidates.extend(reversed(kept_history))  # chronological order
    for item in facts:
        if fits(item):
            candidates.append(item)
    return MemoryContext(
        target=target, classes=classes, items=tuple(candidates), dropped=dropped, tokens=used
    )
