"""Owner-only knowledge search and explicit result exports."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, OwnerAuth
from app.api.errors import problem_response
from app.core.schemas import DataClass, TaskV1
from app.db.models import Task
from app.kb.contracts import KnowledgeError
from app.kb.export import export_task
from app.kb.repository import configured_store
from app.kb.snapshots import load_snapshots
from app.privacy.gate import PrivacyInputError

router = APIRouter(tags=["knowledge"])


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_class: DataClass


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=6000)
    data_class: DataClass
    limit: int = Field(default=5, ge=1, le=16, strict=True)


_INPUT_CODES = frozenset(
    {
        "secret_detected",
        "knowledge_privacy_gate",
        "knowledge_text_too_large",
        "knowledge_text_invalid",
        "invalid_search_limit",
        "invalid_utf8",
        "embedding_input_too_long",
    }
)


def knowledge_error_status(code: str) -> int:
    """Input faults are 422; configuration, contract and embedding faults stay 503."""
    if code.startswith("export_") or code == "knowledge_export_conflict":
        return 409
    if code in _INPUT_CODES:
        return 422
    return 503


def knowledge_problem(exc: KnowledgeError) -> JSONResponse:
    status = knowledge_error_status(exc.code)
    return problem_response(
        status=status,
        title="Knowledge operation unavailable",
        detail="The knowledge operation could not be completed.",
        errors=[{"code": exc.code}],
    )


@router.post("/tasks/{task_id}/export", response_model=None)
def export_result(
    task_id: UUID, body: ExportRequest, session: DbSession, _owner: OwnerAuth
) -> dict[str, str] | JSONResponse:
    try:
        receipt = export_task(session, task_id, data_class=body.data_class)
    except KnowledgeError as exc:
        return knowledge_problem(exc)
    return {
        "document_id": str(receipt.document_id),
        "data_class": receipt.data_class.value,
        "state": receipt.state,
    }


@router.post("/knowledge/search", response_model=None)
def search_knowledge(body: SearchRequest, _owner: OwnerAuth) -> dict[str, object] | JSONResponse:
    try:
        store = configured_store()
        try:
            hits = store.search(body.query, task_class=body.data_class, limit=body.limit)
        finally:
            store.engine.dispose()
    except KnowledgeError as exc:
        return knowledge_problem(exc)
    return {
        "hits": [
            {
                "reference": item.reference,
                "data_class": item.data_class.value,
                "text": item.text,
                "sha256": item.sha256,
            }
            for item in hits
        ]
    }


@router.get("/tasks/{task_id}/contexts", response_model=None)
def task_contexts(
    task_id: UUID, session: DbSession, _owner: OwnerAuth
) -> dict[str, object] | JSONResponse:
    task = session.get(Task, task_id)
    if task is None:
        return problem_response(
            status=404, title="Task not found", detail="The task does not exist."
        )
    try:
        blocks = load_snapshots(session, TaskV1.model_validate(task.payload))
    except PrivacyInputError:
        return problem_response(
            status=409,
            title="Context snapshots unavailable",
            detail="The complete context snapshot set is unavailable.",
        )
    return {
        "contexts": [
            {
                "reference": item.reference,
                "data_class": item.data_class.value,
                "text": item.text,
                "sha256": item.sha256,
            }
            for item in blocks
        ]
    }
