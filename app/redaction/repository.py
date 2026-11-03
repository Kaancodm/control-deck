"""Atomic, task-scoped storage and lookup of local redaction originals."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.schemas import DataClass
from app.db.models import RedactionMap, Task
from app.redaction.engine import RedactionOutcome, redact, rehydrate
from app.redaction.placeholders import parse_placeholder


class RedactionStorageError(ValueError):
    """Storage failure whose message never contains originals or map entries."""


def _load_rows(session: Session, task_id: UUID) -> list[RedactionMap]:
    return list(
        session.scalars(
            select(RedactionMap)
            .where(RedactionMap.task_id == task_id)
            .order_by(RedactionMap.created_at, RedactionMap.id)
        )
    )


def redact_for_task(
    session: Session,
    *,
    task_id: UUID,
    text: str,
    attempt: int,
    blocklist: Sequence[str] = (),
) -> RedactionOutcome:
    """Serialize allocation on the task row and leave committing to the caller."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise RedactionStorageError("A positive attempt is required for redaction storage.")
    try:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update(of=Task))
        if task is None or task.data_class != DataClass.CLOUD_REDACTED:
            raise RedactionStorageError("Task is not eligible for redaction storage.")
        rows = _load_rows(session, task_id)
        existing = {row.placeholder: row.original for row in rows}
        outcome = redact(text, task_id=task_id, existing=existing, blocklist=blocklist)
        identities = {row.original_sha256: row.original for row in rows}
        additions = []
        for placeholder, original in outcome.mapping.items():
            if placeholder in existing:
                if existing[placeholder] != original:
                    raise RedactionStorageError("An existing redaction entry cannot be replaced.")
                continue
            token = parse_placeholder(placeholder)
            digest = sha256(original.encode("utf-8")).hexdigest()
            if digest in identities:
                if identities[digest] != original:
                    raise RedactionStorageError("A redaction digest collision prevents storage.")
                raise RedactionStorageError("A redaction mapping identity conflicts with storage.")
            identities[digest] = original
            additions.append(
                RedactionMap(
                    task_id=task_id,
                    placeholder=placeholder,
                    kind=token.kind.value,
                    original=original,
                    original_sha256=digest,
                    first_attempt=attempt,
                )
            )
        if additions:
            with session.begin_nested():
                session.add_all(additions)
                session.flush()
        return outcome
    except SQLAlchemyError:
        raise RedactionStorageError("Local redaction storage is unavailable.") from None


def rehydrate_for_task(session: Session, *, task_id: UUID, text: str) -> str:
    """Resolve only this task's local map, never a caller-supplied foreign map."""
    try:
        task = session.get(Task, task_id)
        if task is None or task.data_class != DataClass.CLOUD_REDACTED:
            raise RedactionStorageError("Task is not eligible for redaction storage.")
        mapping = {row.placeholder: row.original for row in _load_rows(session, task_id)}
    except SQLAlchemyError:
        raise RedactionStorageError("Local redaction storage is unavailable.") from None
    return rehydrate(text, task_id=task_id, mapping=mapping)
