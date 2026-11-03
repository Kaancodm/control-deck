"""Persist routing metadata and read the append-only claim budget ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.retries import RetryState, canonical_chain, validate_retry_state
from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Task
from app.routing.config import RouterConfig, get_router_config
from app.routing.router import RoutingPlan, route_task

ADMISSION_LOCK = 0x43445254


@dataclass(frozen=True)
class RoutingSnapshot:
    """A read-only UTC snapshot; usage measures claims, not provider quotas."""

    now: datetime
    budget_date: date
    usage: dict[Worker, int]


def routing_snapshot(session: Session) -> RoutingSnapshot:
    """Count today's claims, including active, failed and abandoned attempts.

    Older claims without lane metadata conservatively consume every lane's
    allowance. Claim timestamps use the current database clock independently
    of transaction start time, so crossing midnight never backdates a claim.
    """
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    now = now.astimezone(UTC)
    budget_date = now.date()
    midnight = datetime.combine(budget_date, time.min, tzinfo=UTC)
    lane = Event.detail["lane"].astext
    rows = session.execute(
        select(lane, func.count())
        .where(
            Event.event_type == "task.acquire",
            Event.created_at >= midnight,
            Event.created_at < midnight + timedelta(days=1),
        )
        .group_by(lane)
    )
    usage = {worker: 0 for worker in Worker}
    for recorded_lane, count in rows:
        try:
            worker = Worker(recorded_lane)
        except ValueError:
            for worker in Worker:
                usage[worker] += count
        else:
            usage[worker] += count
    return RoutingSnapshot(now=now, budget_date=budget_date, usage=usage)


def try_lock_budget_admission(session: Session, config: RouterConfig) -> bool:
    """Try the shared ceiling lock before acquiring any task row locks.

    A busy budget admission transaction makes this poll return no work. It does
    not wait while holding rows, and its lock is released by commit or rollback.
    Unlimited claims share the same lock, retaining independent SKIP LOCKED
    claims while preventing a limited admission from racing uncommitted usage.
    """
    if all(lane.daily_attempt_limit is None for lane in config.lanes.values()):
        return bool(session.scalar(select(func.pg_try_advisory_xact_lock_shared(ADMISSION_LOCK))))
    return bool(session.scalar(select(func.pg_try_advisory_xact_lock(ADMISSION_LOCK))))


class RetryMetadataError(ValueError):
    """Reject inconsistent runtime metadata without exposing stored values."""

    def __init__(self) -> None:
        super().__init__("Retry metadata is unavailable or invalid.")


def retry_state_for_task(task: Task) -> RetryState:
    """Read durable progress, including a conservative seed for pre-migration claims."""
    try:
        chain = canonical_chain(TaskV1.model_validate(task.payload))
        if task.retry_state is None:
            if task.attempt > 0:
                if task.lane is None:
                    raise ValueError
                worker = Worker(task.lane)
                state = RetryState(cursor=chain.index(worker), pinned_worker=worker)
            else:
                state = RetryState()
        else:
            state = RetryState.model_validate(task.retry_state)
        validate_retry_state(state, chain)
        if sum(state.rate_limits.values()) + state.internal_retries > task.attempt:
            raise ValueError
        return state
    except (TypeError, ValueError):
        raise RetryMetadataError() from None


def pin_claim_worker(task: Task, worker: Worker) -> None:
    """Remember the actual admitted lane before publishing the committed claim."""
    state = retry_state_for_task(task)
    chain = canonical_chain(TaskV1.model_validate(task.payload))
    if (
        not isinstance(worker, Worker)
        or worker not in chain[state.cursor :]
        or (state.pinned_worker is not None and state.pinned_worker != worker)
    ):
        raise RetryMetadataError()
    task.retry_state = state.model_copy(
        update={"cursor": chain.index(worker), "pinned_worker": worker}
    ).model_dump(mode="json")


def persist_task_route(
    session: Session,
    task: Task,
    *,
    config: RouterConfig | None = None,
    snapshot: RoutingSnapshot | None = None,
    paused_lanes: frozenset[Worker] = frozenset(),
) -> RoutingPlan:
    """Refresh runtime metadata without mutating the validated task payload.

    Creation and approval only preview admission; the queue repeats this under
    the budget lock immediately before reserving a claim.
    """
    config = config if config is not None else get_router_config()
    snapshot = snapshot if snapshot is not None else routing_snapshot(session)
    task_v1 = TaskV1.model_validate(task.payload)
    plan = route_task(
        task_v1,
        config=config,
        usage=snapshot.usage,
        budget_date=snapshot.budget_date,
    )
    try:
        state = retry_state_for_task(task)
    except RetryMetadataError:
        plan = plan.model_copy(
            update={
                "chain": [],
                "models": {},
                "status": "deferred",
                "reason": "retry_metadata_invalid",
            }
        )
    else:
        if task.attempt > 0 and task.retry_state is None:
            # Preserve the legacy lane before a temporary deferral clears task.lane.
            task.retry_state = state.model_dump(mode="json")
        remaining = canonical_chain(task_v1)[state.cursor :]
        if state.pinned_worker is not None:
            remaining = (state.pinned_worker,)
        exclusions = dict(plan.exclusions)
        selected = []
        for worker in plan.chain:
            if worker not in remaining:
                exclusions[worker] = "retry_progress"
            elif worker in paused_lanes:
                exclusions[worker] = "lane_paused"
            else:
                selected.append(worker)
        if selected:
            plan = plan.model_copy(
                update={
                    "chain": selected,
                    "models": {worker: plan.models[worker] for worker in selected},
                    "status": "ready",
                    "reason": None,
                    "exclusions": exclusions,
                }
            )
        elif plan.status != "blocked" or task.attempt > 0 or task.retry_state is not None:
            reasons = {exclusions.get(worker, "retry_no_allowed_lane") for worker in remaining}
            reason = next(iter(reasons)) if len(reasons) == 1 else "lane_unavailable"
            plan = plan.model_copy(
                update={
                    "chain": [],
                    "models": {},
                    "status": "deferred",
                    "reason": reason if remaining else "retry_exhausted",
                    "exclusions": exclusions,
                }
            )
    task.plan = plan.model_dump(mode="json")
    task.lane = plan.chain[plan.cursor].value if plan.status == "ready" else None
    session.flush()
    return plan
