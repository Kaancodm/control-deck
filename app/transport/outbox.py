"""Completion outbox. HTTP 200 records a started run and never acknowledges it."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.schemas import DataClass
from app.db.models import RoutineOutbox, Task
from app.db.settings import read_settings
from app.db.state_machine import TaskState
from app.transport.config import (
    TransportConfigError,
    WakeConfig,
    wake_config_from_env,
    wake_enabled,
)
from app.transport.contract import (
    WAKE_EVENT,
    WAKE_VERSION,
    WakeContractError,
    require_wake_version,
    status_document,
    wake_body,
)
from app.transport.http import StartResponse, post_wake

_MAX_POSTS = 3
_DISPATCH_LIMIT = 8
_SENDABLE = ("pending", "start_failed")


class WakeSender(Protocol):
    def __call__(self, *, url: str, authorization: str, body: bytes) -> StartResponse:
        """Perform one POST and return only the start result."""


def payload_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _now(session: Session) -> datetime:
    moment = session.scalar(select(func.clock_timestamp()))
    if not isinstance(moment, datetime):
        raise WakeContractError("clock_unavailable")
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def note_completion(session: Session, task: Task) -> RoutineOutbox | None:
    """Record one wake when a cloud_ok task reaches review or done.

    The switch defaults off. Other data classes never leave a row. A repeat
    returns the original correlation and does not change the task.
    """
    if task.status not in (TaskState.REVIEW, TaskState.DONE):
        return None
    if not wake_enabled() or task.data_class != DataClass.CLOUD_OK:
        return None
    existing = _by_task(session, task.id)
    if existing is not None:
        return existing
    row = RoutineOutbox(
        task_id=task.id,
        event=WAKE_EVENT,
        schema_version=WAKE_VERSION,
        correlation_id=uuid.uuid4(),
        payload_hash=payload_hash(task.payload),
        task_status=task.status.value,
        state="pending",
        attempt_count=0,
        created_at=_now(session),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = _by_task(session, task.id)
        if raced is None:
            raise
        return raced
    return row


def _by_task(session: Session, task_id: uuid.UUID) -> RoutineOutbox | None:
    return session.scalar(
        select(RoutineOutbox).where(
            RoutineOutbox.task_id == task_id,
            RoutineOutbox.event == WAKE_EVENT,
        )
    )


def dispatch_pending_wakes(
    session_factory: Callable[[], Session],
    *,
    sender: WakeSender | None = None,
    limit: int = 1,
) -> int:
    """Post at most ``limit`` eligible rows. Disabled, paused, or stopped sends nothing."""
    if not wake_enabled():
        return 0
    if type(limit) is not int or not 1 <= limit <= _DISPATCH_LIMIT:
        raise WakeContractError("invalid_limit")
    try:
        config = wake_config_from_env()
        authorization = config.authorization_value()
    except TransportConfigError:
        return 0
    post = sender or post_wake
    sent = 0
    for _ in range(limit):
        if not _send_one(session_factory, config, authorization, post):
            break
        sent += 1
    return sent


def _send_one(
    session_factory: Callable[[], Session],
    config: WakeConfig,
    authorization: str,
    sender: WakeSender,
) -> bool:
    with session_factory() as session:
        settings = read_settings(session)
        if settings.paused or settings.stop_requested:
            return False
        row = session.scalar(
            select(RoutineOutbox)
            .where(
                RoutineOutbox.state.in_(_SENDABLE),
                RoutineOutbox.attempt_count < _MAX_POSTS,
            )
            .order_by(RoutineOutbox.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        body = wake_body(
            task_id=row.task_id,
            payload_hash=row.payload_hash,
            status=row.task_status,
            correlation_id=row.correlation_id,
        )
        row.state = "sending"
        row.attempt_count += 1
        row_id = row.id
        session.commit()
    try:
        result = sender(url=config.url, authorization=authorization, body=body)
    except Exception:
        result = StartResponse("no_response")
    with session_factory() as session:
        current = session.scalar(
            select(RoutineOutbox).where(RoutineOutbox.id == row_id).with_for_update()
        )
        if current is None or current.state != "sending":
            session.commit()
            return True
        if result.kind == "http" and result.status_code == 200:
            current.state = "accepted"
            current.accepted_at = _now(session)
        elif result.kind == "http":
            current.state = "start_failed"
        else:
            current.state = "unknown_start"
        session.commit()
    return True


def acknowledge_wake(
    session: Session,
    *,
    task_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> RoutineOutbox:
    """Record the return ACK. This does not approve the task or deliver a receipt.

    Only a row whose POST already returned 200 can be acknowledged. A repeat
    returns that row unchanged.
    """
    row = session.scalar(
        select(RoutineOutbox)
        .where(RoutineOutbox.task_id == task_id, RoutineOutbox.event == WAKE_EVENT)
        .with_for_update()
    )
    if row is None or row.correlation_id != correlation_id:
        raise WakeContractError("not_found")
    require_wake_version(row.schema_version)
    if row.state == "acknowledged":
        return row
    if row.state != "accepted":
        raise WakeContractError("not_started")
    row.state = "acknowledged"
    row.acknowledged_at = _now(session)
    session.flush()
    return row


def poll_task(session: Session, task_id: uuid.UUID) -> dict[str, object]:
    """Return the bounded status view. Payload text and results stay in the database."""
    task = session.get(Task, task_id)
    if task is None:
        raise WakeContractError("not_found")
    receipt = task.channel_receipt
    wake = _by_task(session, task.id)
    return status_document(
        task_id=task.id,
        status=task.status.value,
        data_class=task.data_class.value,
        payload_hash=payload_hash(task.payload),
        receipt_status=None if receipt is None else receipt.status,
        receipt_transport=None if receipt is None else receipt.transport,
        wake_state=None if wake is None else wake.state,
        correlation_id=None if wake is None else wake.correlation_id,
        wake_status=None if wake is None else wake.task_status,
    )
