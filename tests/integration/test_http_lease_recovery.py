"""HTTP polling releases abandoned capacity before admitting another attempt."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.routes import worker as worker_routes
from app.db.models import Event, Lease, Task
from app.db.session import get_engine
from app.db.state_machine import TaskState
from app.routing.config import RouterConfigurationError
from tests.integration.test_api import (
    api_client as api_client,
)
from tests.integration.test_api import (
    owner_token as owner_token,
)
from tests.integration.test_api import (
    worker_token as worker_token,
)
from tests.integration.test_database import _minimal_task_payload


def _owner_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.environ["DECK_OWNER_TOKEN"]}


def _worker_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.environ["DECK_WORKER_TOKEN_OLLAMA"]}


def _enqueue(client: TestClient) -> str:
    task_uuid = uuid4()
    task_id = str(task_uuid)
    payload = _minimal_task_payload(task_uuid, max_attempts=1)
    created = client.post("/api/v1/tasks", json=payload, headers=_owner_headers())
    assert created.status_code == 201, created.text
    approved = client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner_headers())
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "queued"
    return task_id


def _pull(client: TestClient):
    return client.post("/api/v1/worker/lease", json={"worker": "ollama"}, headers=_worker_headers())


def _first_attempt(client: TestClient, *, running: bool) -> str:
    task_id = _enqueue(client)
    claimed = _pull(client)
    assert claimed.status_code == 200, claimed.text
    if running:
        body = claimed.json()
        started = client.post(
            "/api/v1/worker/start",
            json={
                "task_id": task_id,
                "attempt": 1,
                "lease_id": body["lease"]["lease_id"],
                "worker": "ollama",
            },
            headers=_worker_headers(),
        )
        assert started.status_code == 200, started.text
        assert started.json()["action"] == "continue"
    return task_id


def _expire_lease(session: Session, task_id: str) -> None:
    session.execute(
        text(
            "UPDATE leases SET expires_at=clock_timestamp()-interval '1 second' WHERE task_id=:id"
        ),
        {"id": task_id},
    )
    session.commit()


def _assert_recovered(task_id: str, state: TaskState, event_type: str) -> None:
    # Observe through a separate connection: response cleanup cannot stand in for
    # the recovery commit that must precede the next claim.
    with Session(get_engine(for_tests=True)) as observer:
        task = observer.get(Task, task_id)
        assert task is not None and task.status == state
        assert observer.scalar(select(Lease).where(Lease.task_id == task_id)) is None
        events = observer.scalars(
            select(Event).where(Event.task_id == task_id, Event.event_type == event_type)
        ).all()
        assert len(events) == 1


@pytest.mark.parametrize("running", [False, True])
def test_http_pull_recovers_expired_lease_capacity(
    api_client: TestClient, db_session: Session, running: bool
) -> None:
    first_id = _first_attempt(api_client, running=running)
    second_id = _enqueue(api_client)
    assert _pull(api_client).status_code == 404
    _expire_lease(db_session, first_id)

    claimed = _pull(api_client)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["task"]["id"] == second_id
    _assert_recovered(first_id, TaskState.FAILED, "task.fail")


@pytest.mark.parametrize("running", [False, True])
def test_http_pull_recovers_unacknowledged_cancel_after_lease_expiry(
    api_client: TestClient, db_session: Session, running: bool
) -> None:
    first_id = _first_attempt(api_client, running=running)
    second_id = _enqueue(api_client)
    cancelled = api_client.post(f"/api/v1/tasks/{first_id}/cancel", headers=_owner_headers())
    assert cancelled.status_code == 200, cancelled.text
    # A still-active worker owns capacity until its acknowledgement or lease expiry.
    assert _pull(api_client).status_code == 404
    with Session(get_engine(for_tests=True)) as observer:
        task = observer.get(Task, first_id)
        assert task is not None and task.cancel_requested
        assert task.status == (TaskState.RUNNING if running else TaskState.LEASED)
    _expire_lease(db_session, first_id)

    claimed = _pull(api_client)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["task"]["id"] == second_id
    _assert_recovered(first_id, TaskState.CANCELLED, "task.cancel")


@pytest.mark.parametrize("running", [False, True])
def test_http_pull_commits_owner_deadline_expiry_and_claims_next_task(
    api_client: TestClient, db_session: Session, running: bool
) -> None:
    first_id = _first_attempt(api_client, running=running)
    second_id = _enqueue(api_client)
    db_session.execute(
        text("UPDATE tasks SET deadline_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
        {"id": first_id},
    )
    db_session.commit()

    claimed = _pull(api_client)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["task"]["id"] == second_id
    _assert_recovered(first_id, TaskState.EXPIRED, "task.expire")


def test_http_recovery_commits_before_a_failing_next_claim(
    api_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id = _first_attempt(api_client, running=True)
    _expire_lease(db_session, first_id)

    def unavailable_claim(session: Session, **kwargs: object) -> None:
        assert not session.in_transaction()
        _assert_recovered(first_id, TaskState.FAILED, "task.fail")
        raise RouterConfigurationError()

    monkeypatch.setattr(worker_routes, "claim_prepared_attempt", unavailable_claim)
    response = _pull(api_client)
    assert response.status_code == 503
    _assert_recovered(first_id, TaskState.FAILED, "task.fail")
