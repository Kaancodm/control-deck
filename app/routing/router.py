"""Pure routing decisions from validated tasks and explicit local budget snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import DataClass, TaskV1, Worker
from app.routing.config import RouterConfig


class RoutingPlan(BaseModel):
    """Runtime metadata stored separately from the immutable task payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    chain: list[Worker]
    cursor: Literal[0] = 0
    rule: str
    budget_date: date
    models: dict[Worker, str | None]
    status: Literal["ready", "blocked", "deferred"]
    reason: str | None = None
    exclusions: dict[Worker, str] = Field(default_factory=dict)


def route_task(
    task: TaskV1, *, config: RouterConfig, usage: Mapping[Worker, int], budget_date: date
) -> RoutingPlan:
    """Preserve policy-authorized ordering, with a hard local-only Ollama override."""
    if any(
        not isinstance(worker, Worker) or type(count) is not int or count < 0
        for worker, count in usage.items()
    ):
        raise ValueError("Budget usage requires non-negative integer counts for known workers")
    if type(budget_date) is not date:
        raise ValueError("Budget date must be an explicit calendar date")
    selected_rule = next(
        (
            rule
            for rule in config.rules
            if (not rule.kinds or task.kind in rule.kinds)
            and (not rule.data_classes or task.data_class in rule.data_classes)
        ),
        None,
    )
    exclusions: dict[Worker, str] = {}
    if task.data_class == DataClass.LOCAL_ONLY:
        source = [Worker.OLLAMA]
        exclusions.update(
            (worker, "local_only") for worker in task.fallback_chain if worker != Worker.OLLAMA
        )
    else:
        source = task.fallback_chain
    eligible: list[Worker] = []
    for worker in source:
        reason = None
        if worker in task.policy.forbidden_workers:
            reason = "policy_forbidden"
        elif task.policy.allowed_workers is not None and worker not in task.policy.allowed_workers:
            reason = "policy_not_allowed"
        elif (
            selected_rule is not None
            and selected_rule.allowed_workers is not None
            and worker not in selected_rule.allowed_workers
        ):
            reason = "rule_not_allowed"
        if reason is None:
            eligible.append(worker)
        else:
            exclusions[worker] = reason
    chain: list[Worker] = []
    for worker in eligible:
        # Operational availability must never turn an authorized task into a
        # terminal policy rejection. A later poll may observe re-enabled lanes.
        if not config.lanes[worker].enabled:
            exclusions[worker] = "lane_disabled"
            continue
        limit = config.lanes[worker].daily_attempt_limit
        if limit is not None and usage.get(worker, 0) >= limit:
            exclusions[worker] = "budget_exhausted"
        else:
            chain.append(worker)
    plan_status: Literal["ready", "blocked", "deferred"] = "ready"
    reason = None
    if not eligible:
        plan_status, reason = "blocked", "no_allowed_lane"
    elif not chain:
        unavailable = {exclusions[worker] for worker in eligible}
        reason = next(iter(unavailable)) if len(unavailable) == 1 else "lane_unavailable"
        plan_status = "deferred"
    return RoutingPlan(
        chain=chain,
        rule=selected_rule.id if selected_rule is not None else "default",
        budget_date=budget_date,
        models={
            worker: config.lanes[worker].models.get(
                task.kind.value, config.lanes[worker].models.get("default")
            )
            for worker in chain
        },
        status=plan_status,
        reason=reason,
        exclusions=exclusions,
    )
