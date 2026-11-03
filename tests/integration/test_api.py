"""Integration tests for the HTTP API against PostgreSQL."""

from __future__ import annotations

import copy
import os
import uuid
from collections.abc import Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.db.queue import dequeue_next_queued_task
from app.db.state_machine import TaskState
from tests.integration.test_database import _minimal_task_payload


@pytest.fixture
def worker_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-worker-token-integration"
    monkeypatch.setenv("DECK_WORKER_TOKEN", token)
    return token


@pytest.fixture
def owner_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-owner-token-integration"
    monkeypatch.setenv("DECK_OWNER_TOKEN", token)
    return token


@pytest.fixture
def api_client(
    db_session: Session,
    worker_token: str,
    owner_token: str,
) -> Generator[TestClient, None, None]:
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


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _owner_headers() -> dict[str, str]:
    return _auth_headers(os.environ["DECK_OWNER_TOKEN"])


def test_post_task_awaiting_approval_and_approve_flow(api_client: TestClient) -> None:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    create = api_client.post("/api/v1/tasks", json=payload, headers=_owner_headers())
    assert create.status_code == 201
    body = create.json()["task"]
    assert body["id"] == str(task_id)
    assert body["status"] == TaskState.AWAITING_APPROVAL.value
    assert body["data_class"] == "local_only"

    lease = api_client.post(
        "/api/v1/worker/lease",
        json={},
        headers=_auth_headers(os.environ["DECK_WORKER_TOKEN"]),
    )
    assert lease.status_code == 404

    approved = api_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner_headers())
    assert approved.status_code == 200
    assert approved.json()["status"] == TaskState.QUEUED.value

    lease = api_client.post(
        "/api/v1/worker/lease",
        json={"holder": "itest"},
        headers=_auth_headers(os.environ["DECK_WORKER_TOKEN"]),
    )
    assert lease.status_code == 200
    lease_body = lease.json()
    assert lease_body["task"]["id"] == str(task_id)
    assert lease_body["lease"]["holder"] == "itest"


def test_get_tasks_filter_and_pagination(api_client: TestClient) -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    for task_id in ids:
        api_client.post(
            "/api/v1/tasks",
            json=_minimal_task_payload(task_id),
            headers=_owner_headers(),
        )
        api_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner_headers())

    listing = api_client.get(
        "/api/v1/tasks",
        params={"status": "queued", "limit": 2, "offset": 0},
        headers=_owner_headers(),
    )
    assert listing.status_code == 200
    data = listing.json()
    assert data["total"] >= 3
    assert len(data["items"]) == 2


def test_worker_result_fenced(api_client: TestClient, db_session: Session) -> None:
    task_id = uuid.uuid4()
    api_client.post(
        "/api/v1/tasks",
        json=_minimal_task_payload(task_id),
        headers=_owner_headers(),
    )
    api_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner_headers())

    claimed = dequeue_next_queued_task(db_session, lease_ttl=timedelta(seconds=60))
    assert claimed is not None
    db_session.commit()
    lease_id = claimed.lease.lease_id if claimed.lease else None
    assert lease_id is not None

    result_payload = {
        "schema_version": "result.v1",
        "task_id": str(task_id),
        "attempt": 1,
        "worker": "ollama",
        "status": "succeeded",
        "output": {"text": "done", "files": []},
        "checks": [],
        "usage": {"wall_seconds": 1.0},
        "timing": {
            "started_at": "2025-11-03T00:00:01+00:00",
            "finished_at": "2025-11-03T00:00:02+00:00",
        },
        "error": None,
        "redaction": {"applied": False, "placeholders": 0},
        "payload_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "log_ref": "run/test.log",
    }

    response = api_client.post(
        "/api/v1/worker/result",
        json={"lease_id": str(lease_id), "result": result_payload},
        headers=_auth_headers(os.environ["DECK_WORKER_TOKEN"]),
    )
    assert response.status_code == 200
    assert response.json()["task"]["status"] == TaskState.SUCCEEDED.value

    detail = api_client.get(f"/api/v1/tasks/{task_id}", headers=_owner_headers())
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["results"]) == 1
    assert len(detail_body["events"]) >= 3


def test_cancel_awaiting_approval(api_client: TestClient) -> None:
    task_id = uuid.uuid4()
    api_client.post(
        "/api/v1/tasks",
        json=_minimal_task_payload(task_id),
        headers=_owner_headers(),
    )
    cancel = api_client.post(f"/api/v1/tasks/{task_id}/cancel", headers=_owner_headers())
    assert cancel.status_code == 200
    assert cancel.json()["status"] == TaskState.CANCELLED.value


def test_invalid_task_returns_problem_json(api_client: TestClient) -> None:
    bad = copy.deepcopy(_minimal_task_payload(uuid.uuid4()))
    bad.pop("instructions")
    response = api_client.post("/api/v1/tasks", json=bad, headers=_owner_headers())
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_worker_token_required(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/worker/lease", json={})
    assert response.status_code == 401


def test_owner_token_required(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/tasks", json=_minimal_task_payload(uuid.uuid4()))
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


def test_duplicate_task_id_returns_409_problem_json(api_client: TestClient) -> None:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    first = api_client.post("/api/v1/tasks", json=payload, headers=_owner_headers())
    assert first.status_code == 201
    second = api_client.post("/api/v1/tasks", json=payload, headers=_owner_headers())
    assert second.status_code == 409
    assert second.headers["content-type"].startswith("application/problem+json")
    assert second.json()["title"] == "Task already exists"


def test_owner_approved_queued_task_is_dequeueable(
    api_client: TestClient, db_session: Session
) -> None:
    """Only owner-approved tasks in ``queued`` are pullable."""
    task_id = uuid.uuid4()
    api_client.post(
        "/api/v1/tasks",
        json=_minimal_task_payload(task_id),
        headers=_owner_headers(),
    )
    api_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner_headers())

    claimed = dequeue_next_queued_task(db_session, lease_ttl=timedelta(seconds=60))
    assert claimed is not None
    assert claimed.id == task_id
