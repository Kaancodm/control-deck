"""Explicit owner export with a durable intent across two independent databases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.schemas import DataClass, ResultV1
from app.db.models import Event, Result, Task
from app.db.state_machine import TaskState
from app.kb.contracts import KnowledgeError, allowed_classes, digest_text
from app.kb.repository import KnowledgeStore, check_knowledge_text, configured_store
from app.kb.state import KnowledgeExport


@dataclass(frozen=True)
class ExportReceipt:
    document_id: UUID
    data_class: DataClass
    state: str


def export_task(
    session: Session,
    task_id: UUID,
    *,
    data_class: DataClass,
    store: KnowledgeStore | None = None,
) -> ExportReceipt:
    """Commit the owner's intent, write outside task locks, then audit completion.

    The caller must authenticate the owner and enforce CSRF for browser requests.
    A failed KB write leaves a pending intent. Repeating the same explicit owner
    request resumes it; a changed class/content conflicts instead of duplicating.
    """
    if session.new or session.dirty or session.deleted:
        raise KnowledgeError("export_requires_clean_session")
    if not isinstance(data_class, DataClass):
        raise KnowledgeError("invalid_data_class")
    owned_store = store is None
    store = store or configured_store()
    try:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None or task.status != TaskState.DONE:
            raise KnowledgeError("export_requires_done")
        if task.data_class not in allowed_classes(data_class):
            raise KnowledgeError("export_class_forbidden")
        result = session.scalar(
            select(Result).where(Result.task_id == task_id, Result.attempt == task.attempt)
        )
        if result is None:
            raise KnowledgeError("export_result_unavailable")
        model = ResultV1.model_validate(result.payload)
        if model.status.value != "succeeded" or model.output.files:
            raise KnowledgeError("export_result_unavailable")
        content, title = model.output.text, str(task.payload["title"])
        check_knowledge_text(title, content, data_class)
        digest = digest_text(content)
        document_id = uuid5(NAMESPACE_URL, "control-deck:knowledge-export:" + str(task_id))
        intent = session.get(KnowledgeExport, task_id)
        if intent is not None:
            if (intent.data_class, intent.result_id, intent.content_sha256, intent.document_id) != (
                data_class.value,
                result.id,
                digest,
                document_id,
            ):
                raise KnowledgeError("knowledge_export_conflict")
            if intent.state == "completed":
                session.commit()
                return ExportReceipt(document_id, data_class, "completed")
        else:
            session.add(
                KnowledgeExport(
                    task_id=task_id,
                    document_id=document_id,
                    result_id=result.id,
                    data_class=data_class.value,
                    content_sha256=digest,
                    state="pending",
                )
            )
            session.add(
                Event(
                    task_id=task_id,
                    event_type="kb.export_requested",
                    actor="operator",
                    attempt=task.attempt,
                    from_status=task.status,
                    to_status=task.status,
                    detail={
                        "document_id": str(document_id),
                        "data_class": data_class.value,
                        "content_sha256": digest,
                    },
                )
            )
        session.commit()
        # All exact text is detached before commit; no host transaction spans this call.
        store.put_document(
            document_id=document_id,
            source_task_id=task_id,
            title=title,
            content=content,
            data_class=data_class,
        )
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        intent = session.get(KnowledgeExport, task_id, populate_existing=True)
        if task is None or task.status != TaskState.DONE or intent is None:
            raise KnowledgeError("export_completion_conflict")
        if intent.state != "completed":
            intent.state = "completed"
            intent.completed_at = session.scalar(select(func.clock_timestamp()))
            session.add(
                Event(
                    task_id=task_id,
                    event_type="kb.export",
                    actor="operator",
                    attempt=task.attempt,
                    from_status=task.status,
                    to_status=task.status,
                    detail={
                        "document_id": str(document_id),
                        "data_class": data_class.value,
                        "content_sha256": digest,
                    },
                )
            )
        session.commit()
        return ExportReceipt(document_id, data_class, "completed")
    except Exception:
        session.rollback()
        raise
    finally:
        if owned_store:
            store.engine.dispose()
