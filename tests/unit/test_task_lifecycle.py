"""Review policy and deadline authority checks without a database."""

import uuid
from unittest.mock import Mock

import pytest

from app.db.models import Task
from app.db.state_machine import InvalidTaskTransitionError, TaskState, TaskTransition
from app.db.transitions import TransitionActor, task_requires_review, transition_task


@pytest.mark.parametrize(
    ("policy", "acceptance", "expected"),
    [
        ({}, [], True),
        ({"require_review": True}, [], True),
        ({"require_review": False}, [], False),
        ({"require_review": False}, [{"type": "human"}], True),
        ({"require_review": False}, [{"type": "must_contain", "value": "ok"}], False),
    ],
)
def test_success_review_policy(
    policy: dict[str, object], acceptance: list[dict[str, str]], expected: bool
) -> None:
    task = Task(id=uuid.uuid4(), payload={"policy": policy, "acceptance": acceptance})
    assert task_requires_review(task) is expected


@pytest.mark.parametrize("trigger", [TaskTransition.REVIEW_APPROVE, TaskTransition.REVIEW_REJECT])
@pytest.mark.parametrize("actor", [TransitionActor.SYSTEM, TransitionActor.RUNNER])
def test_review_requires_owner_authority(trigger: TaskTransition, actor: TransitionActor) -> None:
    task = Task(id=uuid.uuid4(), status=TaskState.REVIEW, payload={})
    with pytest.raises(InvalidTaskTransitionError, match="require the owner"):
        transition_task(Mock(), task=task, trigger=trigger, actor=actor)


def test_worker_cannot_expire_task() -> None:
    task = Task(id=uuid.uuid4(), status=TaskState.RUNNING, payload={})
    with pytest.raises(InvalidTaskTransitionError, match="requires system or owner"):
        transition_task(
            Mock(), task=task, trigger=TaskTransition.EXPIRE, actor=TransitionActor.RUNNER
        )
