"""Pure task lifecycle state machine with explicit allowed transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    REVIEW = "review"
    DONE = "done"
    EXPIRED = "expired"
    SUCCEEDED = "succeeded"  # Historical terminal rows retain their original status.
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.SUCCEEDED,
        TaskState.DONE,
        TaskState.EXPIRED,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    }
)


class TaskTransition(StrEnum):
    """Triggers that drive state changes and audit events."""

    ENQUEUE = "enqueue"
    SUBMIT = "submit"
    APPROVE = "approve"
    BLOCK = "block"
    ACQUIRE = "acquire"
    START = "start"
    LEASE_EXPIRED = "lease_expired"
    SUCCEED = "succeed"
    FAIL = "fail"
    RETRY = "retry"
    POLICY_BLOCK = "policy_block"
    CANCEL = "cancel"
    REVIEW_APPROVE = "review_approve"
    REVIEW_REJECT = "review_reject"
    EXPIRE = "expire"


# from_state -> trigger -> to_state
_TRANSITION_TABLE: dict[TaskState | None, dict[TaskTransition, TaskState]] = {
    None: {
        TaskTransition.ENQUEUE: TaskState.QUEUED,
        TaskTransition.SUBMIT: TaskState.AWAITING_APPROVAL,
        TaskTransition.BLOCK: TaskState.BLOCKED,
    },
    TaskState.AWAITING_APPROVAL: {
        TaskTransition.APPROVE: TaskState.QUEUED,
        TaskTransition.BLOCK: TaskState.BLOCKED,
        TaskTransition.EXPIRE: TaskState.EXPIRED,
        TaskTransition.CANCEL: TaskState.CANCELLED,
    },
    TaskState.QUEUED: {
        TaskTransition.ACQUIRE: TaskState.LEASED,
        TaskTransition.EXPIRE: TaskState.EXPIRED,
        TaskTransition.BLOCK: TaskState.BLOCKED,
        TaskTransition.CANCEL: TaskState.CANCELLED,
    },
    # A leased task may fail before it starts, but cannot report success before START.
    TaskState.LEASED: {
        TaskTransition.BLOCK: TaskState.BLOCKED,
        TaskTransition.RETRY: TaskState.QUEUED,
        TaskTransition.START: TaskState.RUNNING,
        TaskTransition.EXPIRE: TaskState.EXPIRED,
        TaskTransition.LEASE_EXPIRED: TaskState.QUEUED,
        TaskTransition.FAIL: TaskState.FAILED,
        TaskTransition.CANCEL: TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskTransition.SUCCEED: TaskState.REVIEW,
        TaskTransition.EXPIRE: TaskState.EXPIRED,
        TaskTransition.FAIL: TaskState.FAILED,
        TaskTransition.RETRY: TaskState.QUEUED,
        TaskTransition.POLICY_BLOCK: TaskState.BLOCKED,
        TaskTransition.LEASE_EXPIRED: TaskState.QUEUED,
        TaskTransition.CANCEL: TaskState.CANCELLED,
    },
    TaskState.REVIEW: {
        TaskTransition.REVIEW_APPROVE: TaskState.DONE,
        TaskTransition.REVIEW_REJECT: TaskState.FAILED,
        TaskTransition.CANCEL: TaskState.CANCELLED,
        TaskTransition.EXPIRE: TaskState.EXPIRED,
    },
}


class InvalidTaskTransitionError(ValueError):
    """Raised when a trigger is not permitted from the current state."""


@dataclass(frozen=True, slots=True)
class TaskTransitionResult:
    """Outcome of a validated transition (used for persistence and events)."""

    transition: TaskTransition
    from_state: TaskState | None
    to_state: TaskState
    event_type: str

    @property
    def from_status(self) -> str | None:
        return self.from_state.value if self.from_state is not None else None

    @property
    def to_status(self) -> str:
        return self.to_state.value


def apply_transition(
    current: TaskState | None,
    trigger: TaskTransition,
) -> TaskTransitionResult:
    """Return the next state for ``trigger`` or raise ``InvalidTaskTransitionError``."""
    if current in TERMINAL_STATES:
        raise InvalidTaskTransitionError(
            f"terminal state {current.value!r} does not accept trigger {trigger.value!r}"
        )

    by_trigger = _TRANSITION_TABLE.get(current)
    if by_trigger is None or trigger not in by_trigger:
        from_label = "initial" if current is None else current.value
        raise InvalidTaskTransitionError(
            f"trigger {trigger.value!r} is not allowed from {from_label!r}"
        )

    to_state = by_trigger[trigger]
    event_type = f"task.{trigger.value}"
    return TaskTransitionResult(
        transition=trigger,
        from_state=current,
        to_state=to_state,
        event_type=event_type,
    )


def allowed_triggers(current: TaskState | None) -> frozenset[TaskTransition]:
    """Triggers permitted from ``current`` (empty for terminal states)."""
    if current in TERMINAL_STATES:
        return frozenset()
    return frozenset(_TRANSITION_TABLE.get(current, {}).keys())
