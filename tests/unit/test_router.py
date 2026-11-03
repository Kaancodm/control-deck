"""Routing is deterministic and cannot broaden task policy permissions."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.schemas import TaskV1, Worker
from app.routing.config import RouterConfig
from app.routing.router import RoutingPlan, route_task
from tests.integration.test_database import _minimal_task_payload

DAY = date(2026, 11, 3)


def _task(**changes: object) -> TaskV1:
    payload = _minimal_task_payload(uuid4())
    payload.update(data_class="cloud_ok", kind="code", fallback_chain=["codex", "claude", "ollama"])
    payload.update(changes)
    return TaskV1.model_validate(payload)


def _route(
    task: TaskV1, config: RouterConfig | None = None, usage: dict[Worker, int] | None = None
) -> RoutingPlan:
    return route_task(task, config=config or RouterConfig(), usage=usage or {}, budget_date=DAY)


@pytest.mark.parametrize("data_class", ["cloud_ok", "cloud_redacted"])
def test_cloud_routing_preserves_chain_and_payload(data_class: str) -> None:
    task = _task(data_class=data_class)
    before = task.model_dump_json()
    config = RouterConfig()
    before_config = config.model_dump_json()
    plan = _route(task, config)
    assert plan.chain == [Worker.CODEX, Worker.CLAUDE, Worker.OLLAMA]
    assert plan.status == "ready" and plan.reason is None
    assert plan.rule == "default" and plan.cursor == 0
    assert plan.models == {worker: None for worker in Worker}
    assert _route(task, config) == plan
    assert task.model_dump_json() == before
    assert config.model_dump_json() == before_config
    assert plan.model_dump(mode="json")["budget_date"] == "2026-11-03"
    with pytest.raises(ValidationError):
        plan.status = "blocked"


@pytest.mark.parametrize(
    "chain", [["claude"], ["codex", "claude"], ["ollama", "claude"], ["ollama"]]
)
def test_local_only_always_routes_to_ollama(chain: list[str]) -> None:
    plan = _route(_task(data_class="local_only", fallback_chain=chain))
    assert plan.chain == [Worker.OLLAMA]
    assert plan.status == "ready"
    assert all(reason == "local_only" for reason in plan.exclusions.values())


@pytest.mark.parametrize(
    "policy,reason",
    [
        ({"allowed_workers": []}, "policy_not_allowed"),
        ({"allowed_workers": ["claude"]}, "policy_not_allowed"),
        ({"forbidden_workers": ["ollama"]}, "policy_forbidden"),
    ],
)
def test_local_override_still_respects_policy(policy: dict, reason: str) -> None:
    plan = _route(_task(data_class="local_only", fallback_chain=["claude"], policy=policy))
    assert plan.status == "blocked"
    assert plan.reason == "no_allowed_lane"
    assert plan.chain == []
    assert plan.exclusions[Worker.OLLAMA] == reason


def test_allowed_workers_filter_does_not_reorder_or_append() -> None:
    task = _task(
        fallback_chain=["claude", "codex"], policy={"allowed_workers": ["ollama", "codex"]}
    )
    assert _route(task).chain == [Worker.CODEX]
    assert _route(_task(policy={"allowed_workers": []})).status == "blocked"


def test_first_matching_rule_is_an_intersection() -> None:
    config = RouterConfig.model_validate(
        {
            "rules": [
                {"id": "other-kind", "kinds": ["draft"], "allowed_workers": []},
                {"id": "other-class", "data_classes": ["local_only"], "allowed_workers": []},
                {
                    "id": "selected",
                    "kinds": ["code"],
                    "data_classes": ["cloud_ok"],
                    "allowed_workers": ["claude", "ollama"],
                },
                {"id": "ignored", "allowed_workers": []},
            ]
        }
    )
    plan = _route(_task(fallback_chain=["codex", "claude"]), config)
    assert plan.chain == [Worker.CLAUDE] and plan.rule == "selected"
    assert plan.exclusions == {Worker.CODEX: "rule_not_allowed"}


def test_rule_cannot_override_local_only() -> None:
    config = RouterConfig.model_validate(
        {"rules": [{"id": "cloud", "allowed_workers": ["claude"]}]}
    )
    plan = _route(_task(data_class="local_only"), config)
    assert plan.status == "blocked" and plan.chain == []
    assert plan.exclusions[Worker.OLLAMA] == "rule_not_allowed"


def test_disabled_lanes_are_temporarily_deferred() -> None:
    config = RouterConfig.model_validate(
        {"lanes": {worker.value: {"enabled": False} for worker in Worker}}
    )
    plan = _route(_task(), config)
    assert plan.status == "deferred" and plan.reason == "lane_disabled"
    assert plan.chain == []
    assert set(plan.exclusions.values()) == {"lane_disabled"}


def test_disabled_and_budget_exhausted_lanes_are_jointly_deferred() -> None:
    config = RouterConfig.model_validate(
        {
            "lanes": {
                "codex": {"enabled": False},
                "claude": {"daily_attempt_limit": 0},
            }
        }
    )
    plan = _route(_task(fallback_chain=["codex", "claude"]), config)
    assert plan.status == "deferred" and plan.reason == "lane_unavailable"
    assert plan.chain == []
    assert plan.exclusions == {
        Worker.CODEX: "lane_disabled",
        Worker.CLAUDE: "budget_exhausted",
    }


def test_disabled_lane_does_not_reorder_available_fallbacks() -> None:
    config = RouterConfig.model_validate({"lanes": {"codex": {"enabled": False}}})
    plan = _route(_task(), config)
    assert plan.status == "ready" and plan.reason is None
    assert plan.chain == [Worker.CLAUDE, Worker.OLLAMA]
    assert plan.exclusions == {Worker.CODEX: "lane_disabled"}


@pytest.mark.parametrize("constraint", ["policy", "rule"])
def test_disabled_unauthorized_lanes_do_not_turn_policy_blocks_into_deferrals(
    constraint: str,
) -> None:
    config = RouterConfig.model_validate(
        {
            "lanes": {worker.value: {"enabled": False} for worker in Worker},
            "rules": [{"id": "none", "allowed_workers": []}] if constraint == "rule" else [],
        }
    )
    task = _task(policy={"allowed_workers": []}) if constraint == "policy" else _task()
    plan = _route(task, config)
    assert plan.status == "blocked" and plan.reason == "no_allowed_lane"
    assert plan.chain == []
    assert "lane_disabled" not in plan.exclusions.values()


def test_only_policy_authorized_disabled_lane_is_deferred() -> None:
    config = RouterConfig.model_validate({"lanes": {"ollama": {"enabled": False}}})
    plan = _route(_task(policy={"allowed_workers": ["ollama"]}), config)
    assert plan.status == "deferred" and plan.reason == "lane_disabled"
    assert plan.exclusions == {
        Worker.CODEX: "policy_not_allowed",
        Worker.CLAUDE: "policy_not_allowed",
        Worker.OLLAMA: "lane_disabled",
    }


@pytest.mark.parametrize(
    "usage,expected,status",
    [
        (0, [Worker.OLLAMA], "ready"),
        (1, [Worker.OLLAMA], "ready"),
        (2, [], "deferred"),
        (3, [], "deferred"),
    ],
)
def test_daily_budget_boundary(usage: int, expected: list[Worker], status: str) -> None:
    config = RouterConfig.model_validate({"lanes": {"ollama": {"daily_attempt_limit": 2}}})
    plan = _route(_task(data_class="local_only"), config, {Worker.OLLAMA: usage})
    assert plan.chain == expected and plan.status == status
    if status == "deferred":
        assert plan.reason == "budget_exhausted"


def test_budget_filters_preserve_remaining_order() -> None:
    config = RouterConfig.model_validate({"lanes": {"codex": {"daily_attempt_limit": 0}}})
    plan = _route(_task(), config)
    assert plan.chain == [Worker.CLAUDE, Worker.OLLAMA]
    assert plan.exclusions == {Worker.CODEX: "budget_exhausted"}


def test_models_select_kind_then_default_then_none() -> None:
    config = RouterConfig.model_validate(
        {
            "lanes": {
                "codex": {"models": {"default": "fixture-default", "code": "fixture-code"}},
                "claude": {"models": {"default": "fixture-fallback"}},
            }
        }
    )
    plan = _route(_task(), config)
    assert plan.models == {
        Worker.CODEX: "fixture-code",
        Worker.CLAUDE: "fixture-fallback",
        Worker.OLLAMA: None,
    }


@pytest.mark.parametrize(
    "usage", [{Worker.OLLAMA: -1}, {Worker.OLLAMA: True}, {Worker.OLLAMA: 1.5}, {"unknown": 1}]
)
def test_bad_budget_snapshot_is_rejected(usage: dict) -> None:
    with pytest.raises(ValueError, match="Budget usage"):
        route_task(_task(), config=RouterConfig(), usage=usage, budget_date=DAY)


def test_budget_date_is_explicit_and_clock_independent() -> None:
    tomorrow = date(2026, 11, 4)
    assert (
        route_task(_task(), config=RouterConfig(), usage={}, budget_date=tomorrow).budget_date
        == tomorrow
    )
    with pytest.raises(ValueError, match="calendar date"):
        route_task(_task(), config=RouterConfig(), usage={}, budget_date=datetime.now(UTC))
