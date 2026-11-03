"""Failure decisions preserve worker restrictions and a bounded retry history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.retries import (
    RetryState,
    canonical_chain,
    decide_failure,
    validate_retry_state,
)
from app.core.schemas import ErrorCode, TaskV1, Worker

CHAIN = (Worker.CLAUDE, Worker.CODEX, Worker.OLLAMA)
PAUSE_ERRORS = (ErrorCode.AUTH_REQUIRED, ErrorCode.POLICY_BLOCKED)
FALLBACK_ERRORS = (
    ErrorCode.TIMEOUT,
    ErrorCode.WORKER_UNAVAILABLE,
    *PAUSE_ERRORS,
)


def _task(**changes: object) -> TaskV1:
    payload = json.loads((Path(__file__).parent / "examples/task_valid_01.json").read_text())
    payload.update(data_class="cloud_ok", fallback_chain=[worker.value for worker in CHAIN])
    payload.update(changes)
    return TaskV1.model_validate(payload)


@pytest.mark.parametrize("worker", list(Worker))
@pytest.mark.parametrize("error_code", list(ErrorCode))
def test_first_failure_matrix(worker: Worker, error_code: ErrorCode) -> None:
    chain = (worker, *(candidate for candidate in Worker if candidate != worker))
    state = RetryState()
    decision = decide_failure(
        error_code=error_code,
        worker=worker,
        attempt=1,
        max_attempts=5,
        chain=chain,
        state=state,
    )
    assert decision.action == ("fallback" if error_code in FALLBACK_ERRORS else "retry")
    assert decision.pause_lane == (error_code in PAUSE_ERRORS)
    assert decision.delay_seconds == (60 if error_code == ErrorCode.RATE_LIMITED else 0)
    assert decision.reason == error_code.value
    assert decision.state.cursor == (1 if error_code in FALLBACK_ERRORS else 0)
    assert decision.state.pinned_worker == (None if error_code in FALLBACK_ERRORS else worker)
    assert decision.state.internal_retries == (1 if error_code == ErrorCode.INTERNAL else 0)
    assert dict(decision.state.rate_limits) == (
        {worker: 1} if error_code == ErrorCode.RATE_LIMITED else {}
    )
    assert state == RetryState()


@pytest.mark.parametrize("max_attempts", range(1, 6))
@pytest.mark.parametrize("error_code", list(ErrorCode))
def test_global_attempt_limit_wins_without_losing_provider_pause(
    max_attempts: int, error_code: ErrorCode
) -> None:
    decision = decide_failure(
        error_code=error_code,
        worker=Worker.CLAUDE,
        attempt=max_attempts,
        max_attempts=max_attempts,
        chain=CHAIN,
        state=RetryState(pinned_worker=Worker.CLAUDE),
    )
    assert decision.action == "fail"
    assert decision.reason == "max_attempts_exhausted"
    assert decision.delay_seconds == 0
    assert decision.pause_lane == (error_code in PAUSE_ERRORS)
    assert decision.state.internal_retries == 0
    assert decision.state.rate_limits.get(Worker.CLAUDE, 0) == (
        1 if error_code == ErrorCode.RATE_LIMITED else 0
    )


def test_rate_limit_retries_after_one_and_four_minutes_then_advances() -> None:
    state = RetryState()
    for attempt, delay, action in ((1, 60, "retry"), (2, 240, "retry"), (3, 0, "fallback")):
        decision = decide_failure(
            error_code=ErrorCode.RATE_LIMITED,
            worker=Worker.CLAUDE,
            attempt=attempt,
            max_attempts=5,
            chain=CHAIN,
            state=state,
        )
        assert decision.action == action
        assert decision.delay_seconds == delay
        assert decision.state.rate_limits[Worker.CLAUDE] == attempt
        state = decision.state
    assert state.cursor == 1
    assert state.pinned_worker is None
    next_worker = decide_failure(
        error_code=ErrorCode.RATE_LIMITED,
        worker=Worker.CODEX,
        attempt=4,
        max_attempts=5,
        chain=CHAIN,
        state=state,
    )
    assert next_worker.action == "retry"
    assert next_worker.delay_seconds == 60
    assert next_worker.state.pinned_worker == Worker.CODEX
    assert dict(next_worker.state.rate_limits) == {Worker.CLAUDE: 3, Worker.CODEX: 1}


def test_acceptance_failure_keeps_actual_worker_until_global_limit() -> None:
    state = RetryState()
    for attempt in range(1, 6):
        decision = decide_failure(
            error_code=ErrorCode.ACCEPTANCE_FAILED,
            worker=Worker.CODEX,
            attempt=attempt,
            max_attempts=5,
            chain=CHAIN,
            state=state,
        )
        assert decision.action == ("retry" if attempt < 5 else "fail")
        assert decision.state.cursor == 1
        assert decision.state.pinned_worker == Worker.CODEX
        assert decision.delay_seconds == 0
        state = decision.state


def test_internal_retry_allowance_is_global_across_worker_fallback() -> None:
    first = decide_failure(
        error_code=ErrorCode.INTERNAL,
        worker=Worker.CLAUDE,
        attempt=1,
        max_attempts=5,
        chain=CHAIN,
        state=RetryState(),
    )
    assert first.action == "retry"
    assert first.state.internal_retries == 1
    fallback = decide_failure(
        error_code=ErrorCode.TIMEOUT,
        worker=Worker.CLAUDE,
        attempt=2,
        max_attempts=5,
        chain=CHAIN,
        state=first.state,
    )
    second = decide_failure(
        error_code=ErrorCode.INTERNAL,
        worker=Worker.CODEX,
        attempt=3,
        max_attempts=5,
        chain=CHAIN,
        state=fallback.state,
    )
    assert second.action == "fail"
    assert second.reason == "internal_retry_exhausted"
    assert second.state.internal_retries == 1


@pytest.mark.parametrize("error_code", [*FALLBACK_ERRORS, ErrorCode.RATE_LIMITED])
def test_exhausted_fallback_chain_fails_and_keeps_provider_pause(error_code: ErrorCode) -> None:
    state = RetryState(
        rate_limits={Worker.OLLAMA: 2} if error_code == ErrorCode.RATE_LIMITED else {}
    )
    decision = decide_failure(
        error_code=error_code,
        worker=Worker.OLLAMA,
        attempt=3,
        max_attempts=5,
        chain=(Worker.OLLAMA,),
        state=state,
    )
    assert decision.action == "fail"
    assert decision.reason == "fallback_exhausted"
    assert decision.state.cursor == 1
    assert decision.state.pinned_worker is None
    assert decision.pause_lane == (error_code in PAUSE_ERRORS)
    validate_retry_state(decision.state, (Worker.OLLAMA,))


@pytest.mark.parametrize("error_code", list(ErrorCode))
def test_local_only_failures_cannot_fall_back_to_cloud(error_code: ErrorCode) -> None:
    chain = canonical_chain(_task(data_class="local_only"))
    decision = decide_failure(
        error_code=error_code,
        worker=Worker.OLLAMA,
        attempt=1,
        max_attempts=5,
        chain=chain,
        state=RetryState(),
    )
    assert chain == (Worker.OLLAMA,)
    assert decision.action == ("fail" if error_code in FALLBACK_ERRORS else "retry")
    assert decision.state.pinned_worker in (None, Worker.OLLAMA)
    assert all(worker == Worker.OLLAMA for worker in decision.state.rate_limits)


@pytest.mark.parametrize("data_class", ["cloud_ok", "cloud_redacted"])
def test_canonical_chain_preserves_original_order_and_payload(data_class: str) -> None:
    task = _task(data_class=data_class, policy={"allowed_workers": ["ollama", "claude"]})
    before = task.model_dump_json()
    assert canonical_chain(task) == (Worker.CLAUDE, Worker.OLLAMA)
    assert task.model_dump_json() == before
    assert canonical_chain(_task(policy={"allowed_workers": []})) == ()
    assert canonical_chain(_task(policy={"allowed_workers": None})) == CHAIN


@pytest.mark.parametrize(
    "policy",
    [
        {"allowed_workers": []},
        {"allowed_workers": ["claude"]},
        {"forbidden_workers": ["ollama"]},
    ],
)
def test_local_override_cannot_broaden_task_permissions(policy: dict[str, list[str]]) -> None:
    task = _task(data_class="local_only", fallback_chain=["claude"], policy=policy)
    assert canonical_chain(task) == ()


def test_state_round_trip_and_decisions_are_immutable_and_deterministic() -> None:
    source = {Worker.CLAUDE: 1}
    state = RetryState(rate_limits=source, pinned_worker=Worker.CLAUDE)
    source[Worker.CLAUDE] = 3
    assert state.rate_limits[Worker.CLAUDE] == 1
    serialized = state.model_dump(mode="json")
    assert serialized == {
        "version": 1,
        "cursor": 0,
        "pinned_worker": "claude",
        "rate_limits": {"claude": 1},
        "internal_retries": 0,
    }
    assert RetryState.model_validate(serialized) == state
    assert RetryState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(TypeError):
        state.rate_limits[Worker.CLAUDE] = 2  # type: ignore[index]
    with pytest.raises(TypeError):
        RetryState().rate_limits[Worker.CLAUDE] = 1  # type: ignore[index]
    with pytest.raises(ValidationError):
        state.cursor = 1
    arguments: dict[str, Any] = dict(
        error_code=ErrorCode.RATE_LIMITED,
        worker=Worker.CLAUDE,
        attempt=2,
        max_attempts=5,
        chain=CHAIN,
        state=state,
    )
    decision = decide_failure(**arguments)
    assert decision == decide_failure(**arguments)
    assert state.model_dump(mode="json") == serialized
    with pytest.raises(ValidationError):
        decision.action = "fail"


@pytest.mark.parametrize(
    "payload",
    [
        {"version": True},
        {"version": "1"},
        {"version": 2},
        {"cursor": -1},
        {"cursor": True},
        {"cursor": "1"},
        {"pinned_worker": "unknown"},
        {"rate_limits": {"claude": -1}},
        {"rate_limits": {"claude": True}},
        {"rate_limits": {"claude": "1"}},
        {"rate_limits": {"unknown": 1}},
        {"internal_retries": -1},
        {"internal_retries": True},
        {"internal_retries": "1"},
        {"unexpected": "untrusted stored value"},
    ],
)
def test_malformed_stored_state_is_rejected_without_input_in_error(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as raised:
        RetryState.model_validate(payload)
    assert "untrusted stored value" not in str(raised.value)


@pytest.mark.parametrize(
    "state,chain",
    [
        (RetryState(), (Worker.CLAUDE, Worker.CLAUDE)),
        (RetryState(cursor=2), (Worker.CLAUDE,)),
        (RetryState(cursor=1, pinned_worker=Worker.CLAUDE), (Worker.CLAUDE,)),
        (RetryState(pinned_worker=Worker.CODEX), CHAIN),
        (RetryState(rate_limits={Worker.CODEX: 1}), (Worker.CLAUDE,)),
        (RetryState(rate_limits={Worker.CLAUDE: 4}), CHAIN),
        (RetryState(internal_retries=2), CHAIN),
    ],
)
def test_inconsistent_runtime_state_is_rejected(
    state: RetryState, chain: tuple[Worker, ...]
) -> None:
    with pytest.raises(ValueError, match="^Retry identity or state is invalid\\.$"):
        validate_retry_state(state, chain)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": 6},
        {"max_attempts": True},
        {"max_attempts": 0},
        {"max_attempts": 6},
        {"error_code": "timeout"},
        {"worker": "claude"},
        {"worker": Worker.CODEX, "chain": (Worker.CLAUDE,)},
        {"chain": ("claude",)},
        {"chain": [Worker.CLAUDE]},
        {"chain": ()},
        {"state": RetryState(cursor=1)},
        {"state": RetryState(cursor=3)},
        {"worker": Worker.CODEX, "state": RetryState(pinned_worker=Worker.CLAUDE)},
        {"state": RetryState(rate_limits={Worker.CLAUDE: 1})},
        {"state": RetryState(internal_retries=1)},
        {"attempt": 4, "state": RetryState(rate_limits={Worker.CLAUDE: 3})},
    ],
)
def test_invalid_failure_identity_history_or_attempt_is_rejected(
    changes: dict[str, object],
) -> None:
    arguments: dict[str, Any] = dict(
        error_code=ErrorCode.TIMEOUT,
        worker=Worker.CLAUDE,
        attempt=1,
        max_attempts=5,
        chain=CHAIN,
        state=RetryState(),
    )
    arguments.update(changes)
    with pytest.raises(ValueError, match="^Retry identity or state is invalid\\.$"):
        decide_failure(**arguments)
