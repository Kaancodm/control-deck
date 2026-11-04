"""Read local lane evidence without probing providers or changing queue state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.health import AttemptHealth, LaneHealthView, LaneObservation, lane_health_view
from app.core.schemas import ErrorCode, Worker
from app.db.models import Event, LaneStatus
from app.db.routing import routing_snapshot
from app.db.settings import read_settings
from app.routing.config import get_router_config


def read_lane_health(session: Session) -> list[LaneHealthView]:
    """Project configured budgets, observations and accepted attempts, never provider quotas."""
    with session.no_autoflush:
        config = get_router_config()
        controls = read_settings(session)
        snapshot = routing_snapshot(session)
        rows = {row.lane: row for row in session.scalars(select(LaneStatus))}
        lane = Event.detail["lane"].astext
        first_claims: dict[str | None, datetime] = dict(
            session.execute(
                select(lane, func.min(Event.created_at))
                .where(Event.event_type == "task.acquire")
                .group_by(lane)
            ).all()
        )
        views = []
        for worker in Worker:
            row = rows.get(worker.value)
            observation = (
                LaneObservation(row.status, row.reason, row.updated_at, row.last_success_at)
                if row is not None
                else None
            )
            attempts = (
                AttemptHealth()
                if row is None
                else AttemptHealth(
                    simulated_attempts=row.simulated_attempts,
                    remote_attempts=row.remote_attempts,
                    unknown_attempts=row.unknown_attempts,
                    completed_attempts=row.completed_attempts,
                    consecutive_failures=row.consecutive_failures,
                    last_error_code=ErrorCode(row.last_error_code) if row.last_error_code else None,
                    last_error_at=row.last_error_at,
                    last_attempt_at=row.last_attempt_at,
                    last_attempt_success_at=row.last_attempt_success_at,
                )
            )
            first = [first_claims[key] for key in (worker.value, None) if first_claims.get(key)]
            views.append(
                lane_health_view(
                    lane=worker,
                    enabled=config.lanes[worker].enabled,
                    paused=controls.paused
                    or controls.stop_requested
                    or controls.lane_paused[worker],
                    daily_attempt_limit=config.lanes[worker].daily_attempt_limit,
                    claims_today=snapshot.usage[worker],
                    observation=observation,
                    attempts=attempts,
                    first_claim_at=min(first) if first else None,
                    now=snapshot.now,
                )
            )
        return views
