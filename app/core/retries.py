"""Pure bounded failure decisions over immutable task policy and runtime state."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
)

from app.core.schemas import DataClass, ErrorCode, TaskV1, Worker

_NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
_INVALID_STATE = "Retry identity or state is invalid."


class RetryState(BaseModel):
    """Durable retry metadata; it never changes the original task payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    cursor: _NonNegativeInt = 0
    pinned_worker: Worker | None = None
    rate_limits: Mapping[Worker, _NonNegativeInt] = Field(
        default_factory=dict, validate_default=True
    )
    internal_retries: _NonNegativeInt = 0

    @field_validator("version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError(_INVALID_STATE)
        return value

    @field_validator("rate_limits")
    @classmethod
    def freeze_rate_limits(cls, value: Mapping[Worker, int]) -> Mapping[Worker, int]:
        return MappingProxyType(dict(value))

    @field_serializer("rate_limits")
    def serialize_rate_limits(self, value: Mapping[Worker, int]) -> dict[Worker, int]:
        return dict(value)


class RetryDecision(BaseModel):
    """A pure plan; the caller supplies DB time, locks and atomic persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["retry", "fallback", "fail"]
    delay_seconds: _NonNegativeInt = 0
    pause_lane: bool = False
    reason: str
    state: RetryState


def canonical_chain(task: TaskV1) -> tuple[Worker, ...]:
    """Return only policy-authorized workers, preserving the immutable task order.

    Availability, budgets and local routing rules are deliberately separate.
    This stable index space keeps fallback progress intact across queue polls.
    """
    if not isinstance(task, TaskV1):
        raise ValueError(_INVALID_STATE)
    source = (Worker.OLLAMA,) if task.data_class == DataClass.LOCAL_ONLY else task.fallback_chain
    return tuple(
        worker
        for worker in source
        if worker not in task.policy.forbidden_workers
        and (task.policy.allowed_workers is None or worker in task.policy.allowed_workers)
    )


def validate_retry_state(state: RetryState, chain: tuple[Worker, ...]) -> None:
    """Reject inconsistent runtime metadata without exposing its contents."""
    if (
        not isinstance(state, RetryState)
        or not isinstance(chain, tuple)
        or any(not isinstance(worker, Worker) for worker in chain)
        or len(chain) != len(set(chain))
        or state.cursor > len(chain)
        or state.internal_retries > 1
        or any(worker not in chain or count > 3 for worker, count in state.rate_limits.items())
    ):
        raise ValueError(_INVALID_STATE)
    if state.pinned_worker is not None and (
        state.cursor == len(chain) or chain[state.cursor] != state.pinned_worker
    ):
        raise ValueError(_INVALID_STATE)


def decide_failure(
    *,
    error_code: ErrorCode,
    worker: Worker,
    attempt: int,
    max_attempts: int,
    chain: tuple[Worker, ...],
    state: RetryState,
) -> RetryDecision:
    """Choose one bounded reaction, without time, I/O or mutation of its inputs.

    ``attempt`` already includes the current claim. Rate-limit counts represent
    earlier failures on each worker; the internal retry allowance is task-wide.
    A provider refusal pauses its lane even when this attempt exhausts the task.
    An acceptance failure never retries: the caller hands the stored result to
    the owner review while the actually executing lane stays recorded.
    Local privacy blocks and cancellation are handled outside this error policy.
    """
    validate_retry_state(state, chain)
    if (
        not isinstance(error_code, ErrorCode)
        or not isinstance(worker, Worker)
        or type(attempt) is not int
        or type(max_attempts) is not int
        or not 1 <= attempt <= max_attempts <= 5
        or worker not in chain
        or sum(state.rate_limits.values()) + state.internal_retries > attempt - 1
    ):
        raise ValueError(_INVALID_STATE)
    index = chain.index(worker)
    if (
        index < state.cursor
        or (state.pinned_worker is not None and state.pinned_worker != worker)
        or state.rate_limits.get(worker, 0) >= 3
    ):
        raise ValueError(_INVALID_STATE)

    pause_lane = error_code in (ErrorCode.AUTH_REQUIRED, ErrorCode.POLICY_BLOCKED)
    counts = dict(state.rate_limits)
    if error_code == ErrorCode.RATE_LIMITED:
        counts[worker] = counts.get(worker, 0) + 1
    current = RetryState(
        cursor=index,
        pinned_worker=worker,
        rate_limits=counts,
        internal_retries=state.internal_retries,
    )

    def fail(reason: str, *, next_state: RetryState = current) -> RetryDecision:
        return RetryDecision(action="fail", pause_lane=pause_lane, reason=reason, state=next_state)

    if error_code == ErrorCode.ACCEPTANCE_FAILED:
        # Contract: acceptance_failed -> review. No retry, fallback or lane pause,
        # independent of the remaining attempt budget.
        return fail(error_code.value)
    if attempt == max_attempts:
        return fail("max_attempts_exhausted")
    if error_code == ErrorCode.RATE_LIMITED and counts[worker] <= 2:
        return RetryDecision(
            action="retry",
            delay_seconds=(60, 240)[counts[worker] - 1],
            reason=error_code.value,
            state=current,
        )
    if error_code == ErrorCode.INTERNAL:
        if state.internal_retries:
            return fail("internal_retry_exhausted")
        retried = RetryState(
            cursor=index,
            pinned_worker=worker,
            rate_limits=counts,
            internal_retries=1,
        )
        return RetryDecision(action="retry", reason=error_code.value, state=retried)

    advanced = RetryState(
        cursor=index + 1,
        pinned_worker=None,
        rate_limits=counts,
        internal_retries=state.internal_retries,
    )
    if advanced.cursor == len(chain):
        return fail("fallback_exhausted", next_state=advanced)
    return RetryDecision(
        action="fallback", pause_lane=pause_lane, reason=error_code.value, state=advanced
    )
