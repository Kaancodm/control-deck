"""Owner review, trusted acceptance, and deadline lifecycle integration tests."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.core.schemas import TaskV1
from app.db.models import Lease, Result, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.session import get_engine
from app.db.state_machine import TaskTransition
from app.db.transitions import TransitionActor, transition_task
from tests.integration.test_database import _minimal_task_payload

_OWNER_HEADERS = {"Authorization": "Bearer test-review-owner"}
_WORKER_HEADERS = {"Authorization": "Bearer test-review-worker"}


@pytest.fixture
def review_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "test-review-owner")
    monkeypatch.setenv("DECK_WORKER_TOKEN", "test-review-worker")
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _create(
    client: TestClient,
    *,
    require_review: bool = True,
    acceptance: list[dict[str, str]] | None = None,
    deadline_at: datetime | None = None,
    max_attempts: int = 3,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id, max_attempts=max_attempts)
    payload["policy"]["require_review"] = require_review
    payload["acceptance"] = acceptance or []
    if deadline_at is not None:
        payload["deadline_at"] = deadline_at.isoformat()
    response = client.post("/api/v1/tasks", json=payload, headers=_OWNER_HEADERS)
    assert response.status_code == 201, response.text
    return task_id


def _claim(client: TestClient, task_id: uuid.UUID) -> uuid.UUID:
    approved = client.post(f"/api/v1/tasks/{task_id}/approve", headers=_OWNER_HEADERS)
    assert approved.status_code == 200, approved.text
    return _next_lease(client, task_id)


def _next_lease(client: TestClient, task_id: uuid.UUID) -> uuid.UUID:
    response = client.post("/api/v1/worker/lease", json={}, headers=_WORKER_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["task"]["id"] == str(task_id)
    return uuid.UUID(response.json()["lease"]["lease_id"])


def _result(
    task_id: uuid.UUID, *, attempt: int = 1, text: str = "accepted output"
) -> dict[str, Any]:
    return {
        "schema_version": "result.v1",
        "task_id": str(task_id),
        "attempt": attempt,
        "worker": "ollama",
        "status": "succeeded",
        "output": {"text": text, "files": []},
        "checks": [],
        "usage": {"wall_seconds": 1.0},
        "timing": {
            "started_at": "2025-11-03T00:00:01+00:00",
            "finished_at": "2025-11-03T00:00:02+00:00",
        },
        "error": None,
        "redaction": {"applied": False, "placeholders": 0},
        "payload_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "log_ref": "runs/test-review.log",
    }


def _submit(
    client: TestClient, task_id: uuid.UUID, lease_id: uuid.UUID, *, attempt: int = 1
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/worker/result",
        json={"lease_id": str(lease_id), "result": _result(task_id, attempt=attempt)},
        headers=_WORKER_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _detail(client: TestClient, task_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(f"/api/v1/tasks/{task_id}", headers=_OWNER_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def _make_deadline_due(session: Session, task_id: uuid.UUID) -> None:
    """Advance owner-set metadata across its boundary without sleeping in a test."""
    task = session.get(Task, task_id)
    assert task is not None
    task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    session.commit()


@pytest.mark.parametrize("boundary", ["start", "acceptance"])
def test_deadline_crossing_during_result_commits_expiry(
    review_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from app.db import worker_ops

    task_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    lease_id = _claim(review_client, task_id)

    def advance_deadline() -> None:
        task = db_session.get(Task, task_id)
        assert task is not None
        task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        db_session.flush()

    if boundary == "start":
        original_transition = worker_ops.fenced_transition_task

        def cross_before_start(*args: Any, **kwargs: Any) -> Any:
            if kwargs["trigger"] == TaskTransition.START:
                advance_deadline()
            return original_transition(*args, **kwargs)

        monkeypatch.setattr(worker_ops, "fenced_transition_task", cross_before_start)
    else:
        original_acceptance = worker_ops.evaluate_acceptance

        def cross_during_acceptance(*args: Any, **kwargs: Any) -> Any:
            advance_deadline()
            return original_acceptance(*args, **kwargs)

        monkeypatch.setattr(worker_ops, "evaluate_acceptance", cross_during_acceptance)

    response = review_client.post(
        "/api/v1/worker/result",
        json={"lease_id": str(lease_id), "result": _result(task_id)},
        headers=_WORKER_HEADERS,
    )
    assert response.status_code == 409, response.text
    # Inspect storage before GET can run another expiry check.
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task is not None
    assert task.status.value == "expired"
    assert task.lease is None
    assert task.results == []
    assert [event.event_type for event in task.events].count("task.expire") == 1


def test_deadline_crossing_during_acquire_skips_to_next_task(
    review_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import queue

    first_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    second_id = _create(review_client)
    for task_id in (first_id, second_id):
        response = review_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_OWNER_HEADERS)
        assert response.status_code == 200
    original_transition = queue.transition_task

    def cross_after_selection(*args: Any, **kwargs: Any) -> Any:
        task = kwargs["task"]
        if kwargs["trigger"] == TaskTransition.ACQUIRE and task.id == first_id:
            task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
            db_session.flush()
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(queue, "transition_task", cross_after_selection)
    claimed = review_client.post("/api/v1/worker/lease", json={}, headers=_WORKER_HEADERS)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["task"]["id"] == str(second_id)
    db_session.expire_all()
    first = db_session.get(Task, first_id)
    assert first is not None
    assert first.status.value == "expired"
    assert first.lease is None
    assert first.attempt == 0


def test_success_requires_owner_review_and_records_approval(
    review_client: TestClient, db_session: Session
) -> None:
    task_id = _create(review_client)
    lease_id = _claim(review_client, task_id)
    assert _submit(review_client, task_id, lease_id)["status"] == "review"
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0

    reviewed = review_client.post(
        f"/api/v1/tasks/{task_id}/review",
        json={"approved": True, "reason": "Meets the acceptance criteria"},
        headers=_OWNER_HEADERS,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "done"
    audit = _detail(review_client, task_id)["events"]
    assert audit[-1]["event_type"] == "task.review_approve"
    assert audit[-1]["from_status"] == "review"
    assert audit[-1]["to_status"] == "done"
    assert audit[-1]["detail"]["actor"] == "operator"

    repeated = review_client.post(
        f"/api/v1/tasks/{task_id}/review", json={"approved": True}, headers=_OWNER_HEADERS
    )
    assert repeated.status_code == 409
    assert len(_detail(review_client, task_id)["events"]) == len(audit)


def test_owner_rejection_finishes_failed(review_client: TestClient) -> None:
    task_id = _create(review_client)
    _submit(review_client, task_id, _claim(review_client, task_id))
    response = review_client.post(
        f"/api/v1/tasks/{task_id}/review",
        json={"approved": False, "reason": "The conclusion is unsupported"},
        headers=_OWNER_HEADERS,
    )
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "failed"
    assert detail["events"][-1]["event_type"] == "task.review_reject"
    assert detail["events"][-1]["detail"]["reason"] == "The conclusion is unsupported"


def test_success_without_review_policy_finishes_done(review_client: TestClient) -> None:
    task_id = _create(review_client, require_review=False)
    assert _submit(review_client, task_id, _claim(review_client, task_id))["status"] == "done"


def test_human_acceptance_requires_review_even_when_policy_disables_it(
    review_client: TestClient,
) -> None:
    task_id = _create(
        review_client,
        require_review=False,
        acceptance=[{"type": "human", "value": "Check quality"}],
    )
    assert _submit(review_client, task_id, _claim(review_client, task_id))["status"] == "review"
    checks = _detail(review_client, task_id)["results"][0]["payload"]["checks"]
    assert checks[0]["type"] == "human"
    assert checks[0]["passed"] is False


def test_core_acceptance_overrides_worker_claims_and_retries_to_limit(
    review_client: TestClient,
) -> None:
    task_id = _create(
        review_client,
        acceptance=[{"type": "must_contain", "value": "required marker"}],
        max_attempts=2,
    )
    lease_id = _claim(review_client, task_id)
    for attempt, expected in [(1, "queued"), (2, "failed")]:
        payload = _result(task_id, attempt=attempt)
        payload["checks"] = [{"type": "must_contain", "passed": True, "detail": "Worker claim"}]
        response = review_client.post(
            "/api/v1/worker/result",
            json={"lease_id": str(lease_id), "result": payload},
            headers=_WORKER_HEADERS,
        )
        assert response.status_code == 200, response.text
        assert response.json()["task"]["status"] == expected
        detail = _detail(review_client, task_id)
        stored = detail["results"][-1]["payload"]
        assert stored["status"] == "failed"
        assert stored["error"]["code"] == "acceptance_failed"
        assert stored["checks"][0]["passed"] is False
        assert len(detail["results"]) == attempt
        event = detail["events"][-1]
        assert event["event_type"] == ("task.retry" if attempt == 1 else "task.fail")
        assert event["detail"]["result_status"] == "failed"
        assert event["detail"]["reason"] == "acceptance_failed"
        if attempt == 1:
            lease_id = _next_lease(review_client, task_id)


def test_worker_cannot_falsely_fail_trusted_acceptance(review_client: TestClient) -> None:
    task_id = _create(
        review_client,
        require_review=False,
        acceptance=[{"type": "must_contain", "value": "accepted"}],
    )
    lease_id = _claim(review_client, task_id)
    payload = _result(task_id)
    payload["checks"] = [{"type": "must_contain", "passed": False, "detail": "Worker claim"}]
    response = review_client.post(
        "/api/v1/worker/result",
        json={"lease_id": str(lease_id), "result": payload},
        headers=_WORKER_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "done"
    assert _detail(review_client, task_id)["results"][0]["payload"]["checks"][0]["passed"] is True


@pytest.mark.parametrize("approved", [None, "true", 1, {}])
def test_review_requires_an_explicit_boolean(review_client: TestClient, approved: Any) -> None:
    task_id = _create(review_client)
    _submit(review_client, task_id, _claim(review_client, task_id))
    response = review_client.post(
        f"/api/v1/tasks/{task_id}/review", json={"approved": approved}, headers=_OWNER_HEADERS
    )
    assert response.status_code == 422
    assert _detail(review_client, task_id)["status"] == "review"


@pytest.mark.parametrize("headers", [{}, _WORKER_HEADERS])
def test_review_requires_owner_authentication(
    review_client: TestClient, headers: dict[str, str]
) -> None:
    task_id = _create(review_client)
    _submit(review_client, task_id, _claim(review_client, task_id))
    response = review_client.post(
        f"/api/v1/tasks/{task_id}/review", json={"approved": True}, headers=headers
    )
    assert response.status_code == 401
    assert _detail(review_client, task_id)["status"] == "review"


@pytest.mark.parametrize("queued", [False, True])
def test_review_rejects_tasks_that_have_no_successful_result(
    review_client: TestClient, queued: bool
) -> None:
    task_id = _create(review_client)
    if queued:
        assert (
            review_client.post(
                f"/api/v1/tasks/{task_id}/approve", headers=_OWNER_HEADERS
            ).status_code
            == 200
        )
    response = review_client.post(
        f"/api/v1/tasks/{task_id}/review", json={"approved": True}, headers=_OWNER_HEADERS
    )
    assert response.status_code == 409


def test_deadline_is_separate_owner_metadata_and_does_not_change_task_v1(
    review_client: TestClient,
) -> None:
    deadline = datetime.now(tz=UTC) + timedelta(hours=1)
    task_id = _create(review_client, deadline_at=deadline)
    detail = _detail(review_client, task_id)
    assert datetime.fromisoformat(detail["deadline_at"]) == deadline
    assert "deadline_at" not in detail["payload"]
    TaskV1.model_validate(detail["payload"])
    listing = review_client.get("/api/v1/tasks", headers=_OWNER_HEADERS)
    assert datetime.fromisoformat(listing.json()["items"][0]["deadline_at"]) == deadline


@pytest.mark.parametrize("deadline", ["2030-01-01T12:00:00", "not-a-date", "0", 0, True])
def test_deadline_requires_a_timezone_aware_datetime(
    review_client: TestClient, deadline: Any
) -> None:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["deadline_at"] = deadline
    response = review_client.post("/api/v1/tasks", json=payload, headers=_OWNER_HEADERS)
    assert response.status_code == 422


def test_deadline_does_not_make_data_class_optional(review_client: TestClient) -> None:
    payload = _minimal_task_payload(uuid.uuid4())
    del payload["data_class"]
    payload["deadline_at"] = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    response = review_client.post("/api/v1/tasks", json=payload, headers=_OWNER_HEADERS)
    assert response.status_code == 422


@pytest.mark.parametrize("state", ["awaiting_approval", "queued", "leased", "running", "review"])
def test_only_due_owner_deadlines_expire_nonterminal_tasks(
    review_client: TestClient, db_session: Session, state: str
) -> None:
    task_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    lease_id = None
    if state == "queued":
        assert (
            review_client.post(
                f"/api/v1/tasks/{task_id}/approve", headers=_OWNER_HEADERS
            ).status_code
            == 200
        )
    elif state in {"leased", "running", "review"}:
        lease_id = _claim(review_client, task_id)
        if state == "running":
            task = db_session.get(Task, task_id)
            assert task is not None
            transition_task(
                db_session,
                task=task,
                trigger=TaskTransition.START,
                lease_id=lease_id,
                actor=TransitionActor.RUNNER,
            )
            db_session.commit()
        elif state == "review":
            _submit(review_client, task_id, lease_id)
    _make_deadline_due(db_session, task_id)
    detail = _detail(review_client, task_id)
    assert detail["status"] == "expired"
    expiry_events = [event for event in detail["events"] if event["event_type"] == "task.expire"]
    assert len(expiry_events) == 1
    assert expiry_events[0]["from_status"] == state
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert _detail(review_client, task_id)["status"] == "expired"
    assert len(_detail(review_client, task_id)["events"]) == len(detail["events"])


def test_overdue_queued_task_is_not_leased(review_client: TestClient, db_session: Session) -> None:
    task_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    assert (
        review_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_OWNER_HEADERS).status_code
        == 200
    )
    _make_deadline_due(db_session, task_id)
    response = review_client.post("/api/v1/worker/lease", json={}, headers=_WORKER_HEADERS)
    assert response.status_code == 404
    assert _detail(review_client, task_id)["status"] == "expired"


@pytest.mark.parametrize("action", ["result", "heartbeat"])
def test_worker_cannot_extend_or_complete_an_overdue_task(
    review_client: TestClient, db_session: Session, action: str
) -> None:
    task_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    lease_id = _claim(review_client, task_id)
    _make_deadline_due(db_session, task_id)
    body = (
        {"lease_id": str(lease_id), "result": _result(task_id)}
        if action == "result"
        else {"lease_id": str(lease_id), "task_id": str(task_id), "extend_seconds": 60}
    )
    response = review_client.post(f"/api/v1/worker/{action}", json=body, headers=_WORKER_HEADERS)
    assert 400 <= response.status_code < 500, response.text
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    assert _detail(review_client, task_id)["status"] == "expired"


def test_review_cannot_approve_an_overdue_task(
    review_client: TestClient, db_session: Session
) -> None:
    task_id = _create(review_client, deadline_at=datetime.now(tz=UTC) + timedelta(hours=1))
    _submit(review_client, task_id, _claim(review_client, task_id))
    _make_deadline_due(db_session, task_id)
    response = review_client.post(
        f"/api/v1/tasks/{task_id}/review", json={"approved": True}, headers=_OWNER_HEADERS
    )
    assert response.status_code == 409
    detail = _detail(review_client, task_id)
    assert detail["status"] == "expired"
    assert all(event["event_type"] != "task.review_approve" for event in detail["events"])


@pytest.mark.parametrize("max_attempts, expected", [(2, "queued"), (1, "failed")])
def test_lease_timeout_without_owner_deadline_is_never_expired(
    review_client: TestClient, db_session: Session, max_attempts: int, expected: str
) -> None:
    task_id = _create(review_client, max_attempts=max_attempts)
    _claim(review_client, task_id)
    task = db_session.get(Task, task_id)
    assert task is not None and task.lease is not None
    task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    db_session.commit()
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    detail = _detail(review_client, task_id)
    assert detail["status"] == expected
    assert detail["deadline_at"] is None
    assert all(event["event_type"] != "task.expire" for event in detail["events"])
    if expected == "queued":
        claimed = dequeue_next_queued_task(db_session, lease_ttl=timedelta(seconds=60))
        assert claimed is not None and claimed.attempt == 2


def test_completed_task_does_not_expire_after_its_deadline(
    review_client: TestClient, db_session: Session
) -> None:
    task_id = _create(
        review_client,
        require_review=False,
        deadline_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    assert _submit(review_client, task_id, _claim(review_client, task_id))["status"] == "done"
    _make_deadline_due(db_session, task_id)
    assert _detail(review_client, task_id)["status"] == "done"


@pytest.mark.parametrize("control", ["heartbeat", "cancel", "expire", "reassign"])
def test_acceptance_allows_concurrent_control_and_rechecks_fencing(
    review_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    control: str,
) -> None:
    from app.db import worker_ops
    from app.db.queue import expire_task_if_due
    from app.db.transitions import request_operator_cancel

    task_id = _create(
        review_client,
        acceptance=[{"type": "must_contain", "value": "accepted"}],
        deadline_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    lease_id = _claim(review_client, task_id)
    original_acceptance = worker_ops.evaluate_acceptance

    def control_during_acceptance(*args: Any, **kwargs: Any) -> Any:
        with Session(get_engine(for_tests=True), autoflush=False) as other:
            # NOWAIT proves the result path holds no row lock during evaluation.
            task = other.scalar(select(Task).where(Task.id == task_id).with_for_update(nowait=True))
            assert task is not None
            if control == "heartbeat":
                worker_ops.refresh_lease_heartbeat(
                    other,
                    lease_id=lease_id,
                    task_id=task_id,
                    extend_by=timedelta(minutes=5),
                )
            elif control == "cancel":
                request_operator_cancel(other, task=task, reason="Concurrent owner request")
            elif control == "expire":
                task.deadline_at = datetime.now(tz=UTC) - timedelta(seconds=1)
                other.flush()
                assert expire_task_if_due(other, task=task)
            else:
                assert task.lease is not None
                task.lease.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
                other.flush()
                transition_task(
                    other,
                    task=task,
                    trigger=TaskTransition.LEASE_EXPIRED,
                    lease_id=lease_id,
                )
                claimed = dequeue_next_queued_task(other, lease_ttl=timedelta(minutes=5))
                assert claimed is not None and claimed.id == task_id
                assert claimed.attempt == 2
            other.commit()
        return original_acceptance(*args, **kwargs)

    monkeypatch.setattr(worker_ops, "evaluate_acceptance", control_during_acceptance)
    response = review_client.post(
        "/api/v1/worker/result",
        json={"lease_id": str(lease_id), "result": _result(task_id)},
        headers=_WORKER_HEADERS,
    )
    expected_status = {"heartbeat": 200, "cancel": 403, "expire": 409, "reassign": 422}
    assert response.status_code == expected_status[control], response.text
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task is not None
    if control == "heartbeat":
        assert task.status.value == "review"
        assert len(task.results) == 1
    else:
        assert task.results == []
        if control == "cancel":
            assert task.cancel_requested is True
        elif control == "expire":
            assert task.status.value == "expired"
        else:
            assert task.attempt == 2
            assert task.lease is not None and task.lease.lease_id != lease_id
