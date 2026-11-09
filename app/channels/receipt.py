"""Durable queue acceptance, distinct from a worker ``task.start``.

The default transport is missing. A missing transport is never recorded as
delivered, and this module does not call an external channel.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ChannelReceipt, Task

RECEIPT_DEADLINE = timedelta(seconds=60)
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")


class ChannelAdapter(Protocol):
    """A caller-supplied delivery attempt. Returning false is not delivery."""

    name: str

    def deliver(self, task_id: uuid.UUID) -> bool:
        """Report whether this adapter actually handed the acceptance off."""


class MissingChannel:
    """Explicit stand-in used when no channel is configured."""

    name = "missing"

    def deliver(self, task_id: uuid.UUID) -> bool:
        return False


def classify_delivery(*, transport: str, reported: bool) -> tuple[str, str, bool]:
    """Map an adapter report to a stored status. Missing transport cannot be delivered."""
    if transport == "missing" or not _NAME.fullmatch(transport):
        return ("accepted", "missing", False)
    if reported is True:
        return ("delivered", transport, True)
    return ("accepted", transport, False)


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("receipt clock must be timezone-aware")
    return now.astimezone(UTC)


def _key_for(task_id: uuid.UUID, idempotency_key: str | None) -> str:
    key = f"task:{task_id}" if idempotency_key is None else idempotency_key
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("invalid idempotency key")
    return key


def _find(session: Session, *, task_id: uuid.UUID, key: str) -> ChannelReceipt | None:
    return session.scalar(
        select(ChannelReceipt).where(
            or_(ChannelReceipt.task_id == task_id, ChannelReceipt.idempotency_key == key)
        )
    )


def accept_task_receipt(
    session: Session,
    task: Task,
    *,
    now: datetime,
    adapter: ChannelAdapter | None = None,
    idempotency_key: str | None = None,
) -> ChannelReceipt:
    """Record that the deck accepted the task into the queue.

    A repeated call returns the original row. It does not move the deadline,
    call the adapter again, or turn a missing transport into delivery.
    """
    moment = _clock(now)
    key = _key_for(task.id, idempotency_key)
    existing = _find(session, task_id=task.id, key=key)
    if existing is not None:
        if existing.task_id != task.id:
            raise ValueError("idempotency key is already in use")
        return existing
    transport = "missing"
    reported = False
    if adapter is not None and adapter.name != "missing":
        if not isinstance(adapter.name, str) or not _NAME.fullmatch(adapter.name):
            raise ValueError("invalid channel name")
        transport = adapter.name
        reported = adapter.deliver(task.id) is True
    status, transport, delivered = classify_delivery(transport=transport, reported=reported)
    row = ChannelReceipt(
        task_id=task.id,
        idempotency_key=key,
        status=status,
        transport=transport,
        accepted_at=moment,
        deadline_at=moment + RECEIPT_DEADLINE,
        delivered_at=moment if delivered else None,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = _find(session, task_id=task.id, key=key)
        if raced is None or raced.task_id != task.id:
            raise ValueError("idempotency key is already in use") from None
        return raced
    return row


def settle_due_receipts(
    session: Session,
    *,
    now: datetime,
    limit: int = 32,
    task_id: uuid.UUID | None = None,
) -> int:
    """Mark accepted receipts whose 60-second deadline has passed.

    ``now`` is the caller's clock. Delivered rows stay delivered. A missing
    transport stays undelivered.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid receipt sweep limit")
    moment = _clock(now)
    query = select(ChannelReceipt).where(
        ChannelReceipt.status == "accepted",
        ChannelReceipt.deadline_at <= moment,
    )
    if task_id is not None:
        query = query.where(ChannelReceipt.task_id == task_id)
    rows = list(
        session.scalars(
            query.order_by(ChannelReceipt.deadline_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for row in rows:
        row.status = "deadline_elapsed"
        row.delivered_at = None
    if rows:
        session.flush()
    return len(rows)


def database_now(session: Session) -> datetime:
    moment = session.scalar(select(func.clock_timestamp()))
    if not isinstance(moment, datetime):
        raise RuntimeError("database clock unavailable")
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def settle_task_receipt(session: Session, task_id: uuid.UUID) -> None:
    """Persist one task's deadline using the database clock."""
    settle_due_receipts(session, now=database_now(session), limit=1, task_id=task_id)


def channel_facts(receipt: ChannelReceipt | None) -> tuple[str | None, str | None]:
    if receipt is None:
        return (None, None)
    return (receipt.status, receipt.transport)
