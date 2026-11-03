"""Transactional lane-health observations without provider output or credentials."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.schemas import Worker
from app.db.models import LaneStatus

_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class LaneHealthState(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    PAUSED = "paused"


class LaneHealthError(ValueError):
    """Reject invalid health metadata without reflecting its value."""

    def __init__(self) -> None:
        super().__init__("Lane health metadata is invalid.")


def _validate_lane(lane: Worker) -> None:
    if not isinstance(lane, Worker):
        raise LaneHealthError()


def update_lane_status(
    session: Session, *, lane: Worker, status: LaneHealthState, reason: str
) -> LaneStatus:
    """Atomically observe one lane; caller commits or rolls back the observation.

    Success denotes a positive health observation, including an explicitly marked
    simulation. It is not proof of a live model run or a known provider quota.
    Failure preserves the last positive observation. The row lock is acquired
    before reading the database clock, so concurrent updates cannot backdate it.
    """
    _validate_lane(lane)
    if not isinstance(status, LaneHealthState) or not isinstance(reason, str):
        raise LaneHealthError()
    if not _REASON_CODE.fullmatch(reason):
        raise LaneHealthError()
    statement = insert(LaneStatus).values(lane=lane.value, status=status.value, reason=reason)
    session.execute(statement.on_conflict_do_nothing(index_elements=[LaneStatus.lane]))
    row = session.scalar(
        select(LaneStatus)
        .where(LaneStatus.lane == lane.value)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    row.status = status.value
    row.reason = reason
    row.updated_at = now
    if status == LaneHealthState.OK:
        row.last_success_at = now
    session.flush()
    return row


def get_lane_status(session: Session, *, lane: Worker) -> LaneStatus | None:
    """Read a known lane without creating a default health observation."""
    _validate_lane(lane)
    return session.scalar(
        select(LaneStatus)
        .where(LaneStatus.lane == lane.value)
        .execution_options(populate_existing=True)
    )


def list_lane_status(session: Session) -> list[LaneStatus]:
    """Read existing observations in stable lane order without inserting rows."""
    return list(
        session.scalars(
            select(LaneStatus).order_by(LaneStatus.lane).execution_options(populate_existing=True)
        )
    )
