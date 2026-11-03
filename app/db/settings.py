"""Validated runner controls serialized with claim admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.schemas import Worker
from app.db.models import Setting, Task
from app.db.routing import ADMISSION_LOCK
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel


class RunnerSettingsError(ValueError):
    """Invalid local controls; values and keys are deliberately not echoed."""

    def __init__(self) -> None:
        super().__init__("Runner settings are unavailable or invalid.")


@dataclass(frozen=True)
class RunnerSettings:
    paused: bool = False
    stop_requested: bool = False
    max_parallel: int = 1
    lane_paused: dict[Worker, bool] = field(
        default_factory=lambda: {worker: False for worker in Worker}
    )


def _validate_value(key: str, value: object) -> None:
    if key == "max_parallel":
        if type(value) is not int or not 1 <= value <= 16:
            raise RunnerSettingsError()
    elif key in ("paused", "stop_requested") or key in {
        f"lanes.{worker.value}.paused" for worker in Worker
    }:
        if type(value) is not bool:
            raise RunnerSettingsError()
    else:
        raise RunnerSettingsError()


def read_settings(session: Session) -> RunnerSettings:
    """Read controls without writes; unknown or malformed stored controls fail closed."""
    values = {
        row.key: row.value
        for row in session.scalars(select(Setting).execution_options(populate_existing=True))
    }
    for key, value in values.items():
        _validate_value(key, value)
    return RunnerSettings(
        paused=values.get("paused", False),
        stop_requested=values.get("stop_requested", False),
        max_parallel=values.get("max_parallel", 1),
        lane_paused={
            worker: values.get(f"lanes.{worker.value}.paused", False) for worker in Worker
        },
    )


def try_lock_admission(session: Session) -> bool:
    """Take the global claim/control lock before touching any task or lease row."""
    return bool(session.scalar(select(func.pg_try_advisory_xact_lock(ADMISSION_LOCK))))


def lock_admission(session: Session) -> None:
    """Serialize a result-driven lane pause before acquiring task or lease locks."""
    session.execute(select(func.pg_advisory_xact_lock(ADMISSION_LOCK)))


def update_settings(session: Session, changes: Mapping[str, object]) -> RunnerSettings:
    """Apply validated owner changes atomically; caller commits the stop decision.

    Call this before acquiring task/lease locks. Clearing stop permits future
    claims but never clears a cancellation already requested on active tasks.
    """
    for key, value in changes.items():
        _validate_value(key, value)
    lock_admission(session)
    for key, value in changes.items():
        statement = insert(Setting).values(key=key, value=value, updated_at=func.clock_timestamp())
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": statement.excluded.value, "updated_at": func.clock_timestamp()},
            )
        )
    session.flush()
    settings = read_settings(session)
    if settings.stop_requested:
        active = session.scalars(
            select(Task)
            .where(
                Task.status.in_((TaskState.LEASED, TaskState.RUNNING)),
                Task.cancel_requested.is_(False),
            )
            .order_by(Task.id)
            .with_for_update(of=Task)
            .execution_options(populate_existing=True)
        ).all()
        for task in active:
            request_operator_cancel(session, task=task, reason="global_stop")
    session.flush()
    return settings
