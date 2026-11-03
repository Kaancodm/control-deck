"""Resolve before persistence, then use only immutable host-side context bytes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.schemas import DataClass, TaskV1
from app.kb.contracts import ContextBlock, KnowledgeError, validate_contexts
from app.kb.repository import configured_store
from app.kb.state import ContextSnapshot
from app.privacy.gate import PrivacyInputError, StoredTaskInspection, inspect_stored_task


def resolve_submission_contexts(task: TaskV1) -> tuple[ContextBlock, ...]:
    """Resolve without a host task/lease transaction; no model sees these bytes."""
    if not task.context_refs:
        return ()
    store = configured_store()
    try:
        return store.resolve(
            tuple(item.ref for item in task.context_refs), task_class=task.data_class
        )
    finally:
        store.engine.dispose()


def store_snapshots(session: Session, task: TaskV1, blocks: tuple[ContextBlock, ...]) -> None:
    validate_contexts(
        blocks, references=tuple(item.ref for item in task.context_refs), task_class=task.data_class
    )
    for position, block in enumerate(blocks):
        session.add(
            ContextSnapshot(
                task_id=task.task_id,
                position=position,
                reference=UUID(block.reference),
                data_class=block.data_class.value,
                content=block.text,
                content_sha256=block.sha256,
            )
        )
    session.flush()


def load_snapshots(session: Session, task: TaskV1) -> tuple[ContextBlock, ...]:
    if not task.context_refs:
        return ()
    rows = session.scalars(
        select(ContextSnapshot)
        .where(ContextSnapshot.task_id == task.task_id)
        .order_by(ContextSnapshot.position)
    ).all()
    try:
        if tuple(row.position for row in rows) != tuple(range(len(rows))):
            raise KnowledgeError("context_set_mismatch")
        blocks = tuple(
            ContextBlock(
                str(row.reference), DataClass(row.data_class), row.content, row.content_sha256
            )
            for row in rows
        )
        validate_contexts(
            blocks,
            references=tuple(item.ref for item in task.context_refs),
            task_class=task.data_class,
        )
        return blocks
    except (KnowledgeError, ValueError) as exc:
        raise PrivacyInputError(
            getattr(exc, "code", "context_snapshot_invalid"), source="context_refs"
        ) from None


def snapshot_digests(blocks: tuple[ContextBlock, ...]) -> list[dict[str, Any]]:
    return [
        {
            "position": position,
            "reference": block.reference,
            "data_class": block.data_class.value,
            "sha256": block.sha256,
        }
        for position, block in enumerate(blocks)
    ]


def inspect_with_snapshots(session: Session, payload: dict[str, Any]) -> StoredTaskInspection:
    try:
        task = TaskV1.model_validate(payload)
        blocks = load_snapshots(session, task)
    except ValidationError:
        return StoredTaskInspection(
            blocking_detail={"reason": "privacy_gate", "code": "invalid_task"}
        )
    except PrivacyInputError as exc:
        return StoredTaskInspection(
            blocking_detail={"reason": "privacy_gate", "code": exc.code, "source": "context_refs"}
        )
    return inspect_stored_task(task.model_dump(mode="json"), contexts=blocks)
