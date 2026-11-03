"""Server-rendered owner workflows using shared business operations directly."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import DbSession
from app.core.schemas import DataClass, TaskV1, Worker
from app.db.failures import list_failed_tasks
from app.db.settings import read_settings, update_settings
from app.db.state_machine import InvalidTaskTransitionError, TaskState
from app.db.ui_auth import (
    SessionCapacityError,
    authenticate_owner,
    check_csrf,
    create_anonymous_session,
    get_ui_session,
    revoke_ui_session,
)
from app.kb.contracts import allowed_classes
from app.kb.snapshots import load_snapshots
from app.kb.state import KnowledgeExport
from app.privacy.config import PrivacyConfigurationError, get_privacy_settings
from app.routing.config import RouterConfigurationError
from app.services.tasks import (
    MIN_UNBLOCK_REASON_CHARS,
    OwnerAction,
    OwnerTaskError,
    act_on_task,
    find_task,
    list_owner_tasks,
    submit_task,
)
from app.ui.auth import (
    SESSION_COOKIE,
    AuthConfigurationError,
    clear_session_cookies,
    get_auth_settings,
    set_session_cookies,
)
from app.ui.chat import ChatError, ChatRequest, UnavailableChat, ask_local
from app.ui.files import MAX_FILE_BYTES, Upload, read_payload, store_uploads
from app.ui.forms import FormError, form_from_draft, task_from_form, validate_draft
from app.ui.middleware import MAX_BODY_BYTES
from app.ui.views import page, preview_context, preview_redirect
from app.workers.lane_status import list_lane_status

router = APIRouter(prefix="/ui", include_in_schema=False)


class UiError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        self.code = code
        self.status = status


def owner(request: Request, session: Session) -> None:
    if get_ui_session(session, request.cookies.get(SESSION_COOKIE)) is None:
        raise UiError("login_required", 401)
    request.state.controls = read_settings(session)


class _MemoryParser(MultiPartParser):
    # The ASGI middleware bounds the complete body first, so no raw upload is spooled to disk.
    spool_max_size = MAX_BODY_BYTES + 1


async def form(request: Request) -> FormData:
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            parsed = await _MemoryParser(
                request.headers,
                request.stream(),
                max_files=16,
                max_fields=64,
                max_part_size=MAX_BODY_BYTES,
            ).parse()
        else:
            parsed = await request.form(max_fields=64)
        for key in parsed:
            if key not in {"files", "context_task"} and len(parsed.getlist(key)) != 1:
                raise UiError("duplicate_form_field")
        return parsed
    except (MultiPartException, ValueError):
        raise UiError("invalid_form") from None


def _value(data: FormData, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise UiError("invalid_form")
    return value


def csrf(request: Request, session: Session, data: FormData, *, anonymous: bool = False) -> None:
    origin = request.headers.get("origin")
    # Chromium can send an opaque Origin for a same-origin form under no-referrer.
    # This exception still requires Fetch Metadata and the session-bound CSRF token.
    opaque_same_origin = origin == "null" and request.headers.get("sec-fetch-site") == "same-origin"
    if (
        origin is not None
        and origin != str(request.base_url).rstrip("/")
        and not opaque_same_origin
    ):
        raise UiError("invalid_csrf", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise UiError("invalid_csrf", 403)
    row = get_ui_session(session, request.cookies.get(SESSION_COOKIE), owner_required=not anonymous)
    supplied = _value(data, "csrf") or request.headers.get("x-csrf-token")
    if row is None or not check_csrf(row, supplied):
        raise UiError("invalid_csrf", 403)


@router.get("")
def index() -> Response:
    return RedirectResponse("/ui/tasks", status_code=303)


@router.get("/login")
def login_page(request: Request, session: DbSession) -> Response:
    settings = get_auth_settings()
    existing = get_ui_session(session, request.cookies.get(SESSION_COOKIE), owner_required=False)
    if existing is not None and existing.authenticated:
        return RedirectResponse("/ui/tasks", status_code=303)
    if existing is not None:
        return page(request, "login.html", owner=False)
    credentials = create_anonymous_session(session, settings)
    response = page(request, "login.html", owner=False, csrf=credentials.csrf_token)
    set_session_cookies(response, credentials, settings)
    return response


@router.post("/login")
async def login(request: Request, session: DbSession) -> Response:
    data = await form(request)
    csrf(request, session, data, anonymous=True)
    settings = get_auth_settings()
    outcome = authenticate_owner(
        session,
        request.cookies.get(SESSION_COOKIE),
        _value(data, "csrf"),
        _value(data, "password"),
        settings,
    )
    session.commit()
    if outcome.credentials is not None:
        response: Response = RedirectResponse("/ui/tasks", status_code=303)
        set_session_cookies(response, outcome.credentials, settings)
        return response
    status = 429 if outcome.code == "login_throttled" else 401
    response = page(request, "login.html", owner=False, error=outcome.code, status=status)
    if outcome.retry_after is not None:
        response.headers["Retry-After"] = str(outcome.retry_after)
    return response


@router.post("/logout")
async def logout(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    revoke_ui_session(session, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/ui/login", status_code=303)
    clear_session_cookies(response, get_auth_settings())
    return response


@router.get("/tasks")
def tasks(request: Request, session: DbSession) -> Response:
    owner(request, session)
    query = request.query_params
    try:
        state = TaskState(query["state"]) if query.get("state") else None
        data_class = DataClass(query["data_class"]) if query.get("data_class") else None
        lane = Worker(query["lane"]) if query.get("lane") else None
        offset = int(query.get("offset", "0"))
        waiting_offset = int(query.get("waiting_offset", "0"))
        rows, total = list_owner_tasks(
            session,
            state=state,
            data_class=data_class,
            lane=lane,
            search=query.get("search", ""),
            offset=offset,
        )
        waiting, waiting_total = list_owner_tasks(session, needs_owner=True, offset=waiting_offset)
    except ValueError:
        raise UiError("invalid_filter") from None
    return page(
        request,
        "tasks.html",
        tasks=rows,
        waiting=waiting,
        waiting_total=waiting_total,
        waiting_offset=waiting_offset,
        total=total,
        offset=offset,
        filters=query,
        states=TaskState,
        classes=DataClass,
        workers=Worker,
    )


@router.get("/tasks/new")
def new_task(request: Request, session: DbSession) -> Response:
    owner(request, session)
    values = {}
    if reference := request.query_params.get("context_ref"):
        try:
            if str(UUID(reference)) != reference:
                raise ValueError
            classification = DataClass(request.query_params.get("data_class", "local_only"))
        except ValueError:
            raise UiError("invalid_context_reference") from None
        values = {"context_refs": reference, "data_class": classification.value}
    return page(request, "task_form.html", **({"values": values} | preview_context(request)))


@router.get("/failed")
def failed(request: Request, session: DbSession) -> Response:
    owner(request, session)
    query = request.query_params
    try:
        offset = int(query.get("offset", "0"))
        data_class = DataClass(query["data_class"]) if query.get("data_class") else None
        rows, total = list_failed_tasks(session, offset=offset, data_class=data_class)
    except ValueError:
        raise UiError("invalid_filter") from None
    return page(request, "failed.html", tasks=rows, total=total, offset=offset)


@router.post("/tasks/draft")
async def draft_form(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    try:
        draft = validate_draft(_value(data, "draft"))
    except FormError:
        raise UiError("invalid_draft") from None
    return preview_redirect(request, "/ui/tasks/new", values=form_from_draft(draft), draft=True)


@router.post("/tasks/new")
async def create_task(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    uploads = []
    try:
        for item in data.getlist("files"):
            if isinstance(item, UploadFile) and item.filename:
                uploads.append(Upload(item.filename, await item.read(MAX_FILE_BYTES + 1)))
        values = {key: value for key, value in data.items() if isinstance(value, str)}
        preview = task_from_form(values, uploads)
        inbox = get_privacy_settings().inbox
        with store_uploads(inbox, preview.task_id, uploads) as files:
            text_count = len(preview.inputs) - len(uploads)
            task_v1 = preview.model_copy(update={"inputs": [*preview.inputs[:text_count], *files]})
            payload = task_v1.model_dump(mode="json")
            payload["inputs"] = [
                item.model_dump(mode="json", exclude_none=True) for item in task_v1.inputs
            ]
            if deadline := values.get("deadline_at"):
                payload["deadline_at"] = deadline
            task = submit_task(session, payload)
            task_id = task.id
            session.commit()
        return RedirectResponse(f"/ui/tasks/{task_id}", status_code=303)
    except (PrivacyConfigurationError, RouterConfigurationError):
        session.rollback()
        return page(
            request, "task_form.html", values={}, error="configuration_unavailable", status=503
        )
    except (ValueError, IntegrityError):
        session.rollback()
        # Rejected raw input may include a secret. Never echo it into an error page.
        return page(request, "task_form.html", values={}, error="submission_rejected", status=422)
    finally:
        await data.close()


@router.get("/tasks/{task_id}")
def detail(request: Request, task_id: UUID, session: DbSession) -> Response:
    owner(request, session)
    task = find_task(session, task_id)
    outputs = []
    for result in sorted(task.results, key=lambda value: value.attempt):
        text = result.payload.get("output", {}).get("text", "")
        # Stored results are authoritative local text, already restored by worker_ops.
        outputs.append({"result": result, "text": text})
    try:
        contexts = load_snapshots(session, TaskV1.model_validate(task.payload))
        context_unavailable = False
    except ValueError:
        contexts, context_unavailable = (), True
    export_classes = [
        value.value for value in DataClass if task.data_class in allowed_classes(value)
    ]
    return page(
        request,
        "task_detail.html",
        task=task,
        outputs=outputs,
        contexts=contexts,
        context_unavailable=context_unavailable,
        export_classes=export_classes,
        knowledge_export=session.get(KnowledgeExport, task.id),
    )


@router.get("/tasks/{task_id}/timeline")
def timeline(request: Request, task_id: UUID, session: DbSession) -> Response:
    owner(request, session)
    return page(request, "partials/timeline.html", task=find_task(session, task_id))


@router.post("/tasks/{task_id}/{action}")
async def action(request: Request, task_id: UUID, action: str, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    action = {"approve-start": "approve", "approve-result": "accept"}.get(action, action)
    if action not in {"approve", "accept", "reject", "cancel", "unblock"}:
        raise UiError("invalid_action", 404)
    if _value(data, "confirm") != "yes":
        raise UiError("confirmation_required")
    reason = _value(data, "reason").strip()
    if action == "reject" and not reason:
        raise UiError("reason_required")
    if action == "unblock" and len(reason) < MIN_UNBLOCK_REASON_CHARS:
        raise UiError("reason_required", 422)
    try:
        act_on_task(session, task_id, cast(OwnerAction, action), reason=reason or None)
    except (OwnerTaskError, InvalidTaskTransitionError, ValueError):
        # Retain committed expiry / safe gate-error observations, as the API does.
        session.commit()
        return page(request, "error.html", error="action_unavailable", status=409)
    return RedirectResponse(f"/ui/tasks/{task_id}", status_code=303)


@router.get("/tasks/{task_id}/payload/{attempt}")
def payload(request: Request, task_id: UUID, attempt: int, session: DbSession) -> Response:
    owner(request, session)
    task = find_task(session, task_id)
    if attempt > task.attempt:
        raise UiError("artifact_unavailable", 404)
    text = read_payload(Path(os.environ.get("DECK_RUNS_DIR", "runs")), task_id, attempt)
    return PlainTextResponse(
        text, headers={"Content-Disposition": 'inline; filename="payload.sent.md"'}
    )


@router.get("/lanes")
def lanes(request: Request, session: DbSession) -> Response:
    owner(request, session)
    return page(
        request,
        "lanes.html",
        controls=read_settings(session),
        workers=Worker,
        observations={row.lane: row for row in list_lane_status(session)},
    )


@router.get("/fragments/status")
def controls_status(request: Request, session: DbSession) -> Response:
    owner(request, session)
    return page(request, "partials/control_status.html", controls=read_settings(session))


@router.post("/control")
async def control(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    key, value = _value(data, "key"), _value(data, "value")
    if key == "stop_requested" and value == "true" and _value(data, "confirmation") != "STOP":
        raise UiError("stop_confirmation_required")
    if value not in {"true", "false"}:
        raise UiError("invalid_control")
    try:
        update_settings(session, {key: value == "true"})
        session.commit()
    except ValueError:
        session.rollback()
        raise UiError("invalid_control") from None
    return RedirectResponse("/ui/lanes", status_code=303)


@router.get("/chat")
def chat_page(request: Request, session: DbSession) -> Response:
    owner(request, session)
    done, _ = list_owner_tasks(session, state=TaskState.DONE, limit=50)
    return page(request, "chat.html", choices=done, **preview_context(request))


@router.post("/chat")
async def chat(request: Request, session: DbSession) -> Response:
    owner(request, session)
    data = await form(request)
    csrf(request, session, data)
    selected = data.getlist("context_task")
    if len(selected) > 4:
        raise UiError("chat_input_limit")
    context = []
    try:
        for value in selected:
            task = find_task(session, UUID(str(value)))
            if task.status != TaskState.DONE:
                raise ChatError("context_not_approved")
            final = max(task.results, key=lambda item: item.attempt, default=None)
            text = final.payload.get("output", {}).get("text", "") if final else ""
            context.append(f"{task.payload['title']}\n{text}")
        chat_request = ChatRequest(_value(data, "prompt"), tuple(context))
        # Never keep authentication or task read transactions open during a subprocess.
        session.commit()
        adapter = getattr(request.app.state, "local_chat", UnavailableChat())
        reply = await run_in_threadpool(ask_local, adapter, chat_request)
    except (ChatError, ValueError):
        return page(request, "chat.html", choices=[], error="local_chat_unavailable", status=503)
    return preview_redirect(
        request,
        "/ui/chat",
        reply=reply,
        draft_json=reply.draft.model_dump_json() if reply.draft else None,
    )


async def ui_error_handler(request: Request, error: Exception) -> Response:
    if isinstance(error, UiError):
        if error.code == "login_required" and request.method == "GET":
            return RedirectResponse("/ui/login", status_code=303)
        return page(request, "error.html", owner=False, error=error.code, status=error.status)
    status = 503 if isinstance(error, (AuthConfigurationError, SessionCapacityError)) else 404
    return page(request, "error.html", owner=False, error="temporarily_unavailable", status=status)
