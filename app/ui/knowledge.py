"""Explicit, session-authenticated owner knowledge workflows."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import DbSession
from app.api.routes.knowledge import knowledge_error_status
from app.core.schemas import DataClass
from app.kb.contracts import KnowledgeError
from app.kb.export import export_task
from app.kb.repository import configured_store
from app.ui.routes import UiError, _value, csrf, form, owner
from app.ui.views import page

router = APIRouter(prefix="/ui", include_in_schema=False)


@router.get("/knowledge")
def knowledge_page(request: Request, session: DbSession) -> Response:
    owner(request, session)
    return page(request, "knowledge.html", hits=None)


@router.post("/knowledge")
async def knowledge_search(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    try:
        csrf(request, session, data)
        try:
            classification = DataClass(_value(data, "data_class"))
        except ValueError:
            raise UiError("data_class_required") from None
        query = _value(data, "query")
        # Release authentication reads before the independent KB operation.
        session.commit()
        store = configured_store()
        try:
            hits = store.search(query, task_class=classification)
        finally:
            store.engine.dispose()
        return page(request, "knowledge.html", hits=hits, selected_class=classification.value)
    except KnowledgeError as exc:
        return page(
            request,
            "knowledge.html",
            hits=None,
            error=exc.code,
            status=knowledge_error_status(exc.code),
        )
    finally:
        await data.close()


@router.post("/tasks/{task_id}/export")
async def export_result(request: Request, task_id: UUID, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    try:
        csrf(request, session, data)
        if _value(data, "confirm") != "yes":
            raise UiError("confirmation_required")
        try:
            classification = DataClass(_value(data, "data_class"))
        except ValueError:
            raise UiError("data_class_required") from None
        session.commit()
        try:
            export_task(session, task_id, data_class=classification)
        except KnowledgeError as exc:
            raise UiError(exc.code, 409) from None
        return RedirectResponse(f"/ui/tasks/{task_id}", status_code=303)
    finally:
        await data.close()
