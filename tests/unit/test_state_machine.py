"""Unit tests for the task lifecycle state machine."""

from __future__ import annotations

import pytest

from app.db.state_machine import (
    InvalidTaskTransitionError,
    TaskState,
    TaskTransition,
    allowed_triggers,
    apply_transition,
)


@pytest.mark.parametrize(
    ("current", "trigger", "expected"),
    [
        (None, TaskTransition.ENQUEUE, TaskState.QUEUED),
        (None, TaskTransition.SUBMIT, TaskState.AWAITING_APPROVAL),
        (None, TaskTransition.BLOCK, TaskState.BLOCKED),
        (TaskState.AWAITING_APPROVAL, TaskTransition.APPROVE, TaskState.QUEUED),
        (TaskState.AWAITING_APPROVAL, TaskTransition.CANCEL, TaskState.CANCELLED),
        (TaskState.QUEUED, TaskTransition.ACQUIRE, TaskState.LEASED),
        (TaskState.QUEUED, TaskTransition.BLOCK, TaskState.BLOCKED),
        (TaskState.QUEUED, TaskTransition.CANCEL, TaskState.CANCELLED),
        (TaskState.LEASED, TaskTransition.START, TaskState.RUNNING),
        (TaskState.LEASED, TaskTransition.LEASE_EXPIRED, TaskState.QUEUED),
        (TaskState.LEASED, TaskTransition.FAIL, TaskState.FAILED),
        (TaskState.LEASED, TaskTransition.CANCEL, TaskState.CANCELLED),
        (TaskState.RUNNING, TaskTransition.SUCCEED, TaskState.REVIEW),
        (TaskState.RUNNING, TaskTransition.FAIL, TaskState.FAILED),
        (TaskState.RUNNING, TaskTransition.RETRY, TaskState.QUEUED),
        (TaskState.RUNNING, TaskTransition.POLICY_BLOCK, TaskState.BLOCKED),
        (TaskState.RUNNING, TaskTransition.LEASE_EXPIRED, TaskState.QUEUED),
        (TaskState.RUNNING, TaskTransition.CANCEL, TaskState.CANCELLED),
        (TaskState.REVIEW, TaskTransition.REVIEW_APPROVE, TaskState.DONE),
        (TaskState.REVIEW, TaskTransition.REVIEW_REJECT, TaskState.FAILED),
        (TaskState.REVIEW, TaskTransition.CANCEL, TaskState.CANCELLED),
        *[
            (state, TaskTransition.EXPIRE, TaskState.EXPIRED)
            for state in (
                TaskState.AWAITING_APPROVAL,
                TaskState.QUEUED,
                TaskState.LEASED,
                TaskState.RUNNING,
                TaskState.REVIEW,
            )
        ],
    ],
)
def test_allowed_transitions(
    current: TaskState | None,
    trigger: TaskTransition,
    expected: TaskState,
) -> None:
    result = apply_transition(current, trigger)
    assert result.to_state == expected
    assert result.from_state == current
    assert result.event_type == f"task.{trigger.value}"


@pytest.mark.parametrize(
    "terminal",
    [
        TaskState.SUCCEEDED,
        TaskState.DONE,
        TaskState.EXPIRED,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    ],
)
def test_terminal_states_reject_all_triggers(terminal: TaskState) -> None:
    for trigger in TaskTransition:
        with pytest.raises(InvalidTaskTransitionError):
            apply_transition(terminal, trigger)


def test_invalid_transition_from_queued() -> None:
    with pytest.raises(InvalidTaskTransitionError):
        apply_transition(TaskState.QUEUED, TaskTransition.START)


def test_allowed_triggers_queued() -> None:
    triggers = allowed_triggers(TaskState.QUEUED)
    assert TaskTransition.ACQUIRE in triggers
    assert TaskTransition.START not in triggers


def test_allowed_triggers_initial() -> None:
    triggers = allowed_triggers(None)
    assert triggers == frozenset(
        {TaskTransition.ENQUEUE, TaskTransition.SUBMIT, TaskTransition.BLOCK}
    )


@pytest.mark.parametrize("trigger", [TaskTransition.REVIEW_APPROVE, TaskTransition.REVIEW_REJECT])
@pytest.mark.parametrize(
    "state", [TaskState.AWAITING_APPROVAL, TaskState.QUEUED, TaskState.RUNNING]
)
def test_review_decisions_only_apply_to_review(state: TaskState, trigger: TaskTransition) -> None:
    with pytest.raises(InvalidTaskTransitionError):
        apply_transition(state, trigger)
