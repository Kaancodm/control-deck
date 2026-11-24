"""Owner clicks for quarantine, approval and correction.

Hashes are the shown bytes from ``app.memory.approval``. Clearing a fact bound
and changing a decision are two posts, two corrections.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.api.deps import DbSession
from app.core.schemas import DataClass
from app.memory.approval import (
    clear_fact_bounds,
    correct_fact,
    correct_message,
    correct_thread_link,
    correct_thread_topic,
    create_owner_thread,
    decide_fact,
    decide_message,
    decide_thread_link,
    decide_thread_topic,
    fact_shown_bytes,
    propose_fact,
    shown_sha256,
    thread_shown_bytes,
)
from app.memory.ingest import contains_secret
from app.memory.models import MemoryFact
from app.memory.policy import ConversationError
from app.ui.auth import SESSION_COOKIE
from app.ui.memory_desk import load_desk
from app.ui.routes import UiError, _value, csrf, form, owner
from app.ui.views import page, preview_context, preview_redirect

router = APIRouter(prefix="/ui", include_in_schema=False)
_HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
_TITLE_MAX = 200


def _memory_page(
    request: Request, session: Session, *, status: int = 200, **extra: object
) -> Response:
    return page(request, "memory.html", status=status, desk=load_desk(session), **extra)


@router.get("/memory")
def memory_page(request: Request, session: DbSession) -> Response:
    owner(request, session)
    extra = preview_context(request) if request.query_params.get("preview") else {}
    return _memory_page(request, session, **extra)


@router.post("/memory/act")
async def memory_act(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await _posted(request, session)
    token = request.cookies.get(SESSION_COOKIE) or ""
    try:
        _act(session, data, token)
        session.commit()
    except ConversationError as exc:
        session.rollback()
        return _memory_page(request, session, status=422, error=exc.code)
    return RedirectResponse("/ui/memory", status_code=303)


@router.post("/memory/propose")
async def memory_propose(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await _posted(request, session)
    try:
        _propose(session, data)
        session.commit()
    except ConversationError as exc:
        session.rollback()
        return _memory_page(request, session, status=422, error=exc.code)
    return RedirectResponse("/ui/memory", status_code=303)


@router.post("/memory/preview")
async def memory_preview(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await _posted(request, session)
    try:
        confirm = _confirm_payload(session, data)
    except ConversationError as exc:
        session.rollback()
        return _memory_page(request, session, status=422, error=exc.code)
    return preview_redirect(request, "/ui/memory", confirm=confirm)


async def _posted(request: Request, session: Session) -> FormData:
    data = await form(request)
    csrf(request, session, data)
    return data


def _act(session: Session, data: FormData, token: str) -> None:
    kind = _choice(data, "kind", {"message", "fact", "thread", "link"})
    action = _choice(data, "action", {"decide", "correct", "clear", "create"})
    digest = _digest(data)
    decision = _value(data, "decision")
    proven = _value(data, "mark_proven") == "yes"
    if action == "create":
        if kind != "thread":
            raise UiError("invalid_form")
        create_owner_thread(
            session,
            title=_value(data, "title"),
            data_class=_class(data),
            session_token=token,
            shown_sha256_hex=digest,
        )
        return
    target = _uuid(_value(data, "target"))
    if kind == "message" and action == "decide":
        decide_message(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
            mark_proven=proven,
        )
    elif kind == "message" and action == "correct":
        correct_message(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
            mark_proven=proven,
        )
    elif kind == "fact" and action == "decide":
        decide_fact(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
            mark_proven=proven,
        )
    elif kind == "fact" and action == "correct":
        correct_fact(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
            mark_proven=proven,
            valid_to=_moment(data),
            superseded_by=_optional_uuid(data, "superseded_by"),
        )
    elif kind == "fact" and action == "clear":
        clear_fact_bounds(
            session,
            target,
            session_token=token,
            shown_sha256_hex=digest,
            clear_valid_to=_value(data, "clear_valid_to") == "yes",
            clear_superseded_by=_value(data, "clear_superseded_by") == "yes",
        )
    elif kind == "thread" and action == "decide":
        decide_thread_topic(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
        )
    elif kind == "thread" and action == "correct":
        correct_thread_topic(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
        )
    elif kind == "link" and action == "decide":
        decide_thread_link(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
        )
    elif kind == "link" and action == "correct":
        correct_thread_link(
            session,
            target,
            decision=decision,
            session_token=token,
            shown_sha256_hex=digest,
        )
    else:
        raise UiError("invalid_form")


def _propose(session: Session, data: FormData) -> None:
    propose_fact(
        session,
        source_message_id=_uuid(_value(data, "source_message_id")),
        original_sentence=_value(data, "original_sentence"),
        subject=_value(data, "subject"),
        predicate=_value(data, "predicate"),
        object_=_value(data, "object"),
    )


def _confirm_payload(session: Session, data: FormData) -> dict[str, object]:
    kind = _choice(data, "kind", {"fact", "thread"})
    if kind == "thread":
        title = _value(data, "title").strip()
        data_class = _class(data)
        if not title or len(title) > _TITLE_MAX:
            raise ConversationError("invalid_title")
        if contains_secret(title):
            raise ConversationError("secret_rejected")
        payload = thread_shown_bytes(title=title, data_class=data_class, decision="approved")
        return {
            "kind": "thread",
            "action": "create",
            "title": title,
            "data_class": data_class,
            "shown": payload.decode("utf-8"),
            "digest": shown_sha256(payload),
        }
    fact = session.scalar(select(MemoryFact).where(MemoryFact.id == _uuid(_value(data, "target"))))
    if fact is None:
        raise ConversationError("not_found")
    if fact.approval_id is None or fact.status not in {"approved", "rejected"}:
        raise ConversationError("not_decided")
    decision = _choice(data, "decision", {"approved", "rejected"})
    proven = _value(data, "mark_proven") == "yes"
    valid_to = _moment(data)
    successor = _optional_uuid(data, "superseded_by")
    if valid_to is None and successor is None:
        raise UiError("invalid_form")
    payload = fact_shown_bytes(
        fact,
        decision=decision,
        mark_proven=proven,
        corrects=fact.approval_id,
        valid_to=valid_to,
        superseded_by=successor,
    )
    return {
        "kind": "fact",
        "action": "correct",
        "target": str(fact.id),
        "decision": decision,
        "mark_proven": proven,
        "valid_to": "" if valid_to is None else valid_to.isoformat(),
        "superseded_by": "" if successor is None else str(successor),
        "shown": payload.decode("utf-8"),
        "digest": shown_sha256(payload),
    }


def _choice(data: FormData, key: str, allowed: set[str]) -> str:
    value = _value(data, key)
    if value not in allowed:
        raise UiError("invalid_form")
    return value


def _digest(data: FormData) -> str:
    value = _value(data, "shown_sha256")
    if _HEX64.fullmatch(value) is None:
        raise UiError("invalid_form")
    return value


def _class(data: FormData) -> str:
    value = _value(data, "data_class")
    if value not in {item.value for item in DataClass}:
        raise UiError("invalid_form")
    return value


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise UiError("invalid_form") from None


def _optional_uuid(data: FormData, key: str) -> UUID | None:
    raw = _value(data, key).strip()
    if not raw:
        return None
    return _uuid(raw)


def _moment(data: FormData) -> datetime | None:
    raw = _value(data, "valid_to").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise UiError("invalid_form") from None
    if parsed.tzinfo is None:
        raise UiError("invalid_form")
    return parsed
