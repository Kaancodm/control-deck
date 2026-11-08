"""Owner pages for dialogue, cards, and Deep Search. Orders reuse the task gate."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from starlette.datastructures import FormData

from app.api.deps import DbSession
from app.assistant.cards import (
    AssistantCardV1,
    card_document,
    card_from_form,
    copy_card,
    create_card,
    ensure_presets,
    list_cards,
    parse_card_json,
    substitute,
    update_card,
)
from app.assistant.deepsearch import parse_corpus, run_deep_search
from app.assistant.dialogue import (
    current_profile,
    recent_turns,
    reply_to_owner,
    store_turn,
    write_profile,
)
from app.assistant.errors import AssistantError
from app.assistant.orders import describe_search, submit_order
from app.assistant.providers import resolve_search
from app.assistant.records import AssistantCard, AssistantOrderKey, placeholders_of
from app.assistant.status import CHANNEL_GAPS, heartbeat_is_current, project_order
from app.db.models import Event, Task
from app.db.state_machine import TaskState
from app.ui.routes import ParsedForm, csrf, owner
from app.ui.views import page, preview_context, preview_redirect

router = APIRouter(prefix="/ui/assistant", include_in_schema=False)

_CHANNEL = {
    "name": "Kaan",
    "state": "missing",
    "detail": "Kein Transport. Keine Quittung und keine erfundene Freigabe.",
}


def _text(data: FormData, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise AssistantError("assistant_input")
    return value


def _context(session: DbSession) -> dict[str, Any]:
    ensure_presets(session)
    cards = list_cards(session)
    profile = current_profile(session)
    keys = {str(card.id): str(uuid.uuid4()) for card in cards}
    keys["search"] = str(uuid.uuid4())
    return {
        "cards": cards,
        "profile": profile,
        "turns": recent_turns(session),
        "gaps": CHANNEL_GAPS,
        "channel": _CHANNEL,
        "order_keys": keys,
        "placeholders_of": placeholders_of,
        "reply": None,
        "search": None,
        "notice": None,
    }


def _page(request: Request, session: DbSession, *, status: int = 200, **extra: Any) -> Response:
    """Merge page data once. A preview may override the empty reply, search, or notice."""
    context = _context(session)
    context.update(preview_context(request))
    context.update(extra)
    return page(request, "assistant.html", status=status, **context)


def _fail(request: Request, session: DbSession, exc: AssistantError) -> Response:
    session.rollback()
    status = 404 if exc.code == "order_missing" else 422
    return _page(request, session, status=status, error=exc.code)


@router.get("")
def assistant_page(request: Request, session: DbSession) -> Response:
    owner(request, session)
    return _page(request, session)


@router.post("/talk")
def talk(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        reply = reply_to_owner(_text(data, "message"), data_class=_text(data, "data_class"))
        if _text(data, "persist") == "yes":
            store_turn(session, reply, owner_text=_text(data, "message").strip())
            return RedirectResponse("/ui/assistant", status_code=303)
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(request, "/ui/assistant", reply=reply)


@router.post("/profile")
def profile(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        row = write_profile(session, _text(data, "notes"), actor="owner")
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(
        request, "/ui/assistant", notice=f"Profil gespeichert, Version {row.version}"
    )


@router.post("/cards")
def add_card(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        card = create_card(session, _card_form(data))
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(
        request, "/ui/assistant", notice=f"Karte gespeichert, Version {card.version}"
    )


@router.post("/cards/import")
def import_card(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        card = create_card(session, parse_card_json(_text(data, "raw")))
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(
        request, "/ui/assistant", notice=f"Karte importiert, Version {card.version}"
    )


@router.post("/cards/{card_id}")
def edit_card(
    card_id: uuid.UUID, request: Request, session: DbSession, data: ParsedForm
) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        card = update_card(session, card_id, _card_form(data))
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(
        request, "/ui/assistant", notice=f"Karte gespeichert, Version {card.version}"
    )


@router.post("/cards/{card_id}/copy")
def duplicate_card(
    card_id: uuid.UUID, request: Request, session: DbSession, data: ParsedForm
) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        card = copy_card(session, card_id)
    except AssistantError as exc:
        return _fail(request, session, exc)
    return preview_redirect(
        request, "/ui/assistant", notice=f"Kopie gespeichert, Version {card.version}"
    )


@router.get("/cards/{card_id}/export")
def export_card(card_id: uuid.UUID, request: Request, session: DbSession) -> Response:
    owner(request, session)
    try:
        card = session.get(AssistantCard, card_id)
        if card is None:
            raise AssistantError("order_missing")
        body = json.dumps(card_document(card), ensure_ascii=False)
    except AssistantError as exc:
        return _fail(request, session, exc)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="assistant-card.json"'},
    )


@router.post("/cards/{card_id}/run")
def run_card(
    card_id: uuid.UUID, request: Request, session: DbSession, data: ParsedForm
) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        card = session.get(AssistantCard, card_id)
        if card is None:
            raise AssistantError("order_missing")
        values = {
            key.removeprefix("ph_"): _text(data, key)
            for key in data
            if isinstance(key, str) and key.startswith("ph_")
        }
        rendered = substitute(card.template, values, placeholders_of(card.placeholders))
        instructions = f"{rendered}\nEntwurf, kein Live-Modell."[:8000]
        task = submit_order(
            session,
            key=_text(data, "idempotency_key"),
            title=card.name,
            instructions=instructions,
            data_class=card.data_class,
            goal=card.name,
            category=card.category,
        )
    except AssistantError as exc:
        return _fail(request, session, exc)
    return RedirectResponse(f"/ui/assistant/orders/{task.id}", status_code=303)


@router.post("/search")
def search(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        question = _text(data, "question")
        data_class = _text(data, "data_class")
        result = run_deep_search(
            question=question,
            data_class=data_class,
            sources=parse_corpus(_text(data, "corpus")),
            web=resolve_search(),
        )
        if _text(data, "save_order") != "yes":
            return preview_redirect(request, "/ui/assistant", search=result)
        task = submit_order(
            session,
            key=_text(data, "idempotency_key"),
            title=bounded_title(question),
            instructions=describe_search(question, result),
            data_class=result.data_class,
            goal=bounded_title(question),
            category="research",
        )
    except AssistantError as exc:
        return _fail(request, session, exc)
    return RedirectResponse(f"/ui/assistant/orders/{task.id}", status_code=303)


@router.get("/orders/{task_id}")
def order_page(task_id: uuid.UUID, request: Request, session: DbSession) -> Response:
    owner(request, session)
    key = session.scalar(select(AssistantOrderKey).where(AssistantOrderKey.task_id == task_id))
    task = session.get(Task, task_id)
    if key is None or task is None:
        return page(
            request, "assistant.html", error="order_missing", status=404, **_context(session)
        )
    events = tuple(
        session.scalars(select(Event.event_type).where(Event.task_id == task.id).order_by(Event.id))
    )
    expires = task.lease.expires_at if task.lease is not None else None
    approved = task.status == TaskState.DONE and "task.review_approve" in events
    view = project_order(
        status=task.status.value,
        event_types=events,
        heartbeat_current=heartbeat_is_current(expires_at=expires, now=datetime.now(UTC)),
        result_approved=approved,
        failure_code=_failure_code(task),
    )
    labels = task.payload.get("labels", {})
    return page(
        request,
        "assistant_order.html",
        task=task,
        view=view,
        labels=labels if isinstance(labels, dict) else {},
        gaps=CHANNEL_GAPS,
        channel=_CHANNEL,
    )


def _card_form(data: FormData) -> AssistantCardV1:
    return card_from_form(
        name=_text(data, "name"),
        category=_text(data, "category"),
        template=_text(data, "template"),
        data_class=_text(data, "data_class"),
        placeholders=_text(data, "placeholders"),
    )


def bounded_title(question: str) -> str:
    compact = " ".join(question.split())
    title = f"Suche: {compact}" if compact else "Suche"
    return title[:80]


def _failure_code(task: Task) -> str | None:
    if not task.results:
        return None
    latest = max(task.results, key=lambda item: item.attempt)
    error = latest.payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return str(error["code"])
    return None
