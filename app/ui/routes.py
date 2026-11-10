"""Server-rendered owner workflows using shared business operations directly."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import DbSession
from app.assistant.providers import activation_status
from app.audit.presentation import (
    ACTORS,
    AUTH_EVENT_TYPES,
    OPERATION_TYPES,
    TASK_EVENT_TYPES,
    AuditFilter,
    audit_csv,
)
from app.audit.repository import list_audit
from app.audit.service import read_lane_health
from app.channels.receipt import channel_facts, settle_task_receipt
from app.core.schemas import DataClass, TaskV1, Worker
from app.db.failures import list_failed_tasks
from app.db.models import ChannelReceipt, Task
from app.db.settings import read_settings, update_settings
from app.db.state_machine import InvalidTaskTransitionError, TaskState
from app.db.ui_auth import (
    SessionCapacityError,
    authenticate_owner,
    check_csrf,
    create_anonymous_session,
    get_ui_session,
    limit_login_page,
    owner_login_locked,
    revoke_all_ui_sessions,
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
    client_address,
    get_auth_settings,
    set_session_cookies,
)
from app.ui.chat import ChatError, ChatRequest, UnavailableChat, ask_local
from app.ui.files import MAX_FILE_BYTES, UiFileError, Upload, read_payload, store_uploads
from app.ui.forms import FormError, form_from_draft, task_from_form, validate_draft
from app.ui.middleware import MAX_BODY_BYTES
from app.ui.overview import (
    claims_summary,
    feature_flags,
    lane_rows,
    latest_outcome,
    network_nodes,
    open_points,
    workflow_steps,
)
from app.ui.progress import AttemptProgress, attempt_progress
from app.ui.views import HOME_PATH, page, preview_context, preview_redirect

router = APIRouter(prefix="/ui", include_in_schema=False)
root_router = APIRouter(include_in_schema=False)


@root_router.get("/")
def root() -> Response:
    # Fixed internal target; no query, cookie or session is read here.
    return RedirectResponse("/ui", status_code=303)


class UiError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        self.code = code
        self.status = status


def owner(request: Request, session: Session) -> None:
    if get_ui_session(session, request.cookies.get(SESSION_COOKIE)) is None:
        raise UiError("login_required", 401)
    request.state.controls = read_settings(session)
    try:
        request.state.lane_health = read_lane_health(session)
    except RouterConfigurationError:
        request.state.lane_health = []
        request.state.health_unavailable = True


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


async def parsed_form(request: Request) -> AsyncIterator[FormData]:
    data = await form(request)
    try:
        yield data
    finally:
        await data.close()


ParsedForm = Annotated[FormData, Depends(parsed_form)]


def _value(data: FormData, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise UiError("invalid_form")
    return value


def csrf_origin(request: Request) -> None:
    """Check request provenance without acquiring a session lock."""
    origin = request.headers.get("origin")
    expected_origin = getattr(request.state, "public_origin", None) or str(request.base_url).rstrip(
        "/"
    )
    # Chromium can send an opaque Origin for a same-origin form under no-referrer.
    # This exception still requires Fetch Metadata and the session-bound CSRF token.
    opaque_same_origin = origin == "null" and request.headers.get("sec-fetch-site") == "same-origin"
    if origin is not None and origin != expected_origin and not opaque_same_origin:
        raise UiError("invalid_csrf", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise UiError("invalid_csrf", 403)


def csrf(request: Request, session: Session, data: FormData, *, anonymous: bool = False) -> None:
    csrf_origin(request)
    row = get_ui_session(session, request.cookies.get(SESSION_COOKIE), owner_required=not anonymous)
    supplied = _value(data, "csrf") or request.headers.get("x-csrf-token")
    if row is None or not check_csrf(row, supplied):
        raise UiError("invalid_csrf", 403)


@router.get("")
def index() -> Response:
    return RedirectResponse(HOME_PATH, status_code=303)


@router.get("/login")
def login_page(request: Request, session: DbSession) -> Response:
    settings = get_auth_settings()
    existing = get_ui_session(session, request.cookies.get(SESSION_COOKIE), owner_required=False)
    if existing is not None and existing.authenticated:
        return RedirectResponse(HOME_PATH, status_code=303)
    if owner_login_locked(session):
        return page(request, "login.html", owner=False, login_locked=True, status=423)
    retry_after = limit_login_page(session, settings, client=client_address(request))
    if retry_after is not None:
        response = page(request, "login.html", owner=False, error="login_throttled", status=429)
        response.headers["Retry-After"] = str(retry_after)
        return response
    if existing is not None:
        return page(request, "login.html", owner=False)
    try:
        credentials = create_anonymous_session(session, settings, client=client_address(request))
    except SessionCapacityError as error:
        # A normal return commits the GET counter even when no new session can be issued.
        response = page(request, "error.html", owner=False, error="login_throttled", status=429)
        response.headers["Retry-After"] = str(error.retry_after)
        return response
    response = page(request, "login.html", owner=False, csrf=credentials.csrf_token)
    set_session_cookies(response, credentials, settings)
    return response


@router.post("/login")
def login(request: Request, session: DbSession, data: ParsedForm) -> Response:
    csrf(request, session, data, anonymous=True)
    settings = get_auth_settings()
    # Throttling is keyed by the connection peer; forwarding headers are never consulted.
    outcome = authenticate_owner(
        session,
        request.cookies.get(SESSION_COOKIE),
        _value(data, "csrf"),
        _value(data, "password"),
        settings,
        client=client_address(request),
    )
    session.commit()
    if outcome.credentials is not None:
        response: Response = RedirectResponse(HOME_PATH, status_code=303)
        set_session_cookies(response, outcome.credentials, settings)
        return response
    if outcome.code == "login_locked":
        return page(request, "login.html", owner=False, login_locked=True, status=423)
    status = 429 if outcome.code == "login_throttled" else 401
    response = page(request, "login.html", owner=False, error=outcome.code, status=status)
    if outcome.retry_after is not None:
        response.headers["Retry-After"] = str(outcome.retry_after)
    return response


@router.post("/logout")
def logout(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    revoke_ui_session(session, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/ui/login", status_code=303)
    clear_session_cookies(response, get_auth_settings())
    return response


@router.post("/logout-all")
def logout_all(request: Request, session: DbSession, data: ParsedForm) -> Response:
    csrf_origin(request)
    settings = get_auth_settings()
    # Do not call owner()/csrf() first: activity updates would take a session lock
    # before the global login-state lock used by authentication and revocation.
    if not revoke_all_ui_sessions(
        session,
        request.cookies.get(SESSION_COOKIE),
        _value(data, "csrf") or request.headers.get("x-csrf-token"),
        settings,
    ):
        raise UiError("invalid_csrf", 403)
    session.commit()
    response = RedirectResponse("/ui/login", status_code=303)
    clear_session_cookies(response, settings)
    return response


_ACTIVE_FOCUS = frozenset({TaskState.QUEUED, TaskState.LEASED, TaskState.RUNNING})


@router.get("/overview")
def overview(request: Request, session: DbSession) -> Response:
    """Owner overview. Only existing read functions; every number has a stored source."""
    owner(request, session)
    waiting, waiting_total = list_owner_tasks(session, needs_owner=True, limit=5)
    _, blocked_total = list_owner_tasks(session, state=TaskState.BLOCKED, limit=1)
    latest, _ = list_owner_tasks(session, limit=1)
    recent = list_audit(session, AuditFilter(limit=5))
    backups = list_audit(
        session, AuditFilter(source="operation", event_type="operation.backup", limit=10)
    )
    restores = list_audit(
        session, AuditFilter(source="operation", event_type="operation.restore", limit=10)
    )
    focus = waiting[0] if waiting else None
    if focus is None and latest and latest[0].status in _ACTIVE_FOCUS:
        focus = latest[0]
    lanes = request.state.lane_health
    health_unavailable = getattr(request.state, "health_unavailable", False)
    flags = feature_flags()
    last_backup = latest_outcome(backups.items)
    last_restore = latest_outcome(restores.items)
    rows = lane_rows(lanes)
    points = open_points(
        controls=request.state.controls,
        waiting_total=waiting_total,
        blocked_total=blocked_total,
        lanes=lanes,
        health_unavailable=health_unavailable,
        flags=flags,
        last_backup=last_backup,
        last_restore=last_restore,
    )
    return page(
        request,
        "overview.html",
        waiting_total=waiting_total,
        focus=focus,
        steps=workflow_steps(None if focus is None else focus.status.value),
        claims=claims_summary(lanes),
        lane_rows=rows,
        flags=flags,
        activation=activation_status(),
        open_points=points,
        audit=recent.items,
        csv_url="/ui/audit.csv?limit=5",
        last_backup=last_backup,
        last_restore=last_restore,
        net=network_nodes(lanes=lanes, flags=flags, last_backup=last_backup),
        nav_counts={"lanes": len(rows), "open": len(points)},
    )


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
def draft_form(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    try:
        draft = validate_draft(_value(data, "draft"))
    except FormError:
        raise UiError("invalid_draft") from None
    return preview_redirect(request, "/ui/tasks/new", values=form_from_draft(draft), draft=True)


@router.post("/tasks/new")
def create_task(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
    csrf(request, session, data)
    uploads = []
    try:
        for item in data.getlist("files"):
            if isinstance(item, UploadFile) and item.filename:
                uploads.append(Upload(item.filename, item.file.read(MAX_FILE_BYTES + 1)))
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
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        # Rejected raw input may include a secret. Never echo it into an error page; only the
        # fixed server-side code is shown so the owner learns what to correct.
        code = exc.code if isinstance(exc, (FormError, UiFileError)) else "submission_rejected"
        return page(request, "task_form.html", values={}, error=code, status=422)


def _attempt_progress(task: Task, session: Session) -> AttemptProgress:
    """Read stored events and the channel receipt. Worker start is a separate line."""
    settle_task_receipt(session, task.id)
    receipt = session.scalar(select(ChannelReceipt).where(ChannelReceipt.task_id == task.id))
    channel_status, channel_transport = channel_facts(receipt)
    result = max(task.results, key=lambda row: row.attempt, default=None)
    result_status = None
    error_code = None
    profile = None
    if result is not None:
        stored = result.payload.get("status")
        result_status = stored if isinstance(stored, str) else None
        profile = result.execution_profile
        raw_error = result.payload.get("error")
        if isinstance(raw_error, dict):
            code = raw_error.get("code")
            error_code = code if isinstance(code, str) else None
    fallback = task.status == TaskState.QUEUED and any(
        isinstance(event.detail, dict) and event.detail.get("retry_action") == "fallback"
        for event in task.events
    )
    started: list[int] = []
    for event in task.events:
        if event.event_type != "task.start" or not isinstance(event.detail, dict):
            continue
        attempt = event.detail.get("attempt")
        if type(attempt) is int:
            started.append(attempt)
    return attempt_progress(
        status=task.status.value,
        current_attempt=task.attempt,
        started_attempts=tuple(started),
        result_attempt=None if result is None else result.attempt,
        result_status=result_status,
        error_code=error_code,
        execution_profile=profile,
        fallback_pending=fallback,
        channel_status=channel_status,
        channel_transport=channel_transport,
    )


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
        progress=_attempt_progress(task, session),
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
def action(
    request: Request, task_id: UUID, action: str, session: DbSession, data: ParsedForm
) -> Response:
    owner(request, session)
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
        health=request.state.lane_health,
    )


def _audit_filter(request: Request) -> AuditFilter:
    query = request.query_params
    if any(len(query.getlist(key)) != 1 for key in query):
        raise UiError("invalid_filter")
    try:
        return AuditFilter.model_validate({key: value for key, value in query.items() if value})
    except ValueError:
        raise UiError("invalid_filter") from None


@router.get("/audit")
def audit(request: Request, session: DbSession) -> Response:
    owner(request, session)
    selection = _audit_filter(request)
    result = list_audit(session, selection)
    return page(
        request,
        "audit.html",
        audit=result,
        filters=request.query_params,
        workers=Worker,
        actors=sorted(ACTORS),
        event_types=sorted(
            TASK_EVENT_TYPES
            | AUTH_EVENT_TYPES
            | {f"operation.{value}" for value in OPERATION_TYPES}
        ),
        csv_url="/ui/audit.csv" + ("?" + str(request.query_params) if request.query_params else ""),
    )


@router.get("/audit.csv")
def audit_export(request: Request, session: DbSession) -> Response:
    owner(request, session)
    result = list_audit(session, _audit_filter(request))
    return Response(
        audit_csv(result.items),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


@router.get("/fragments/status")
def controls_status(request: Request, session: DbSession) -> Response:
    owner(request, session)
    return page(request, "partials/control_status.html", controls=read_settings(session))


@router.post("/control")
def control(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
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
def chat(request: Request, session: DbSession, data: ParsedForm) -> Response:
    owner(request, session)
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
        reply = ask_local(adapter, chat_request)
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
    if isinstance(error, SessionCapacityError):
        response = page(request, "error.html", owner=False, error="login_throttled", status=429)
        response.headers["Retry-After"] = str(error.retry_after)
        return response
    status = 503 if isinstance(error, AuthConfigurationError) else 404
    return page(request, "error.html", owner=False, error="temporarily_unavailable", status=status)
