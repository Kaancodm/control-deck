"""Transactional lane-health observations without provider output or credentials."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.health import AttemptHealth, completed_attempt
from app.core.schemas import ErrorCode, ResultStatus, Worker
from app.db.models import LaneStatus
from app.workers.contracts import ExecutionProfile

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
    session: Session,
    *,
    lane: Worker,
    status: LaneHealthState,
    reason: str,
    probe_generation: int | None = None,
) -> LaneStatus:
    """Atomically observe one lane; caller commits or rolls back the observation.

    Success denotes a positive health observation, including an explicitly marked
    simulation. It is not proof of a live model run or a known provider quota.
    Failure preserves the last positive observation. Start an external probe with
    ``probe_pending`` and commit its generation before I/O. Its result may update
    only that generation and retains the probe start time for freshness checks.
    Any newer observation invalidates older probes, including across processes.
    """
    _validate_lane(lane)
    if not isinstance(status, LaneHealthState) or not isinstance(reason, str):
        raise LaneHealthError()
    if not _REASON_CODE.fullmatch(reason):
        raise LaneHealthError()
    if probe_generation is not None and (type(probe_generation) is not int or probe_generation < 1):
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
    if probe_generation is not None and row.probe_generation != probe_generation:
        return row
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    row.status = status.value
    row.reason = reason
    if probe_generation is None:
        row.probe_generation += 1
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


def record_completed_attempt(
    session: Session,
    *,
    lane: Worker,
    status: ResultStatus,
    error_code: ErrorCode | None,
    execution_profile: ExecutionProfile = "simulation",
) -> None:
    """Count once after final result fencing, in the same transaction as its result.

    Callers hold the task lock and have inserted the unique task/attempt result.
    Never acquire admission or task locks after taking this lane lock.
    """
    _validate_lane(lane)
    session.execute(
        insert(LaneStatus)
        .values(
            lane=lane.value, status=LaneHealthState.UNAVAILABLE.value, reason="attempt_recorded"
        )
        .on_conflict_do_nothing(index_elements=[LaneStatus.lane])
    )
    row = session.scalar(
        select(LaneStatus)
        .where(LaneStatus.lane == lane.value)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    state = completed_attempt(
        AttemptHealth(
            simulated_attempts=row.simulated_attempts,
            remote_attempts=row.remote_attempts,
            unknown_attempts=row.unknown_attempts,
            completed_attempts=row.completed_attempts,
            consecutive_failures=row.consecutive_failures,
            last_error_code=ErrorCode(row.last_error_code) if row.last_error_code else None,
            last_error_at=row.last_error_at,
            last_attempt_at=row.last_attempt_at,
            last_attempt_success_at=row.last_attempt_success_at,
        ),
        status=status,
        error_code=error_code,
        recorded_at=now,
        execution_profile=execution_profile,
    )
    row.simulated_attempts = state.simulated_attempts
    row.remote_attempts = state.remote_attempts
    row.unknown_attempts = state.unknown_attempts
    row.completed_attempts = state.completed_attempts
    row.consecutive_failures = state.consecutive_failures
    row.last_error_code = state.last_error_code.value if state.last_error_code else None
    row.last_error_at = state.last_error_at
    row.last_attempt_at = state.last_attempt_at
    row.last_attempt_success_at = state.last_attempt_success_at
    session.flush()


def list_lane_status(session: Session) -> list[LaneStatus]:
    """Read existing observations in stable lane order without inserting rows."""
    return list(
        session.scalars(
            select(LaneStatus).order_by(LaneStatus.lane).execution_options(populate_existing=True)
        )
    )
