"""Authenticated lanes fence HTTP worker requests before private task access."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Event, Lease, Result, Task
from tests.integration.test_api import (
    api_client as api_client,
)
from tests.integration.test_api import (
    owner_token as owner_token,
)
from tests.integration.test_api import (
    worker_token as worker_token,
)
from tests.integration.test_runner_api import _claim, _enqueue, _identity, _result, _worker


@pytest.mark.parametrize("authenticated_lane", ["claude", "codex"])
def test_cloud_token_cannot_impersonate_ollama_to_read_local_text(
    api_client: TestClient, db_session: Session, authenticated_lane: str
) -> None:
    original = "Synthetic local-only marker withheld from remote workers"
    task_id = _enqueue(api_client, instructions=original)
    before_events = db_session.scalar(select(func.count()).select_from(Event))
    response = api_client.post(
        "/api/v1/worker/lease",
        json={"worker": "ollama"},
        headers=_worker(authenticated_lane),
    )
    assert response.status_code == 403, response.text
    assert original not in response.text
    assert task_id not in response.text
    task = db_session.get(Task, UUID(task_id))
    assert task is not None
    assert task.status.value == "queued"
    assert task.attempt == 0
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == before_events
    # Honest cloud claims remain ineligible, while the authenticated local lane
    # can receive its original text after committing its prepared attempt.
    honest = api_client.post(
        "/api/v1/worker/lease",
        json={"worker": authenticated_lane},
        headers=_worker(authenticated_lane),
    )
    assert honest.status_code == 404
    assert original in _claim(api_client)["payload_text"]


@pytest.mark.parametrize("authenticated_lane", ["claude", "codex"])
@pytest.mark.parametrize("action", ["start", "heartbeat", "result"])
def test_cloud_token_cannot_control_or_submit_as_local_lane(
    api_client: TestClient, db_session: Session, authenticated_lane: str, action: str
) -> None:
    task_id = _enqueue(api_client)
    envelope = _claim(api_client)
    if action == "result":
        started = api_client.post(
            "/api/v1/worker/start", json=_identity(envelope), headers=_worker()
        )
        assert started.status_code == 200
        body = {"lease_id": envelope["lease"]["lease_id"], "result": _result(envelope)}
    else:
        body = _identity(envelope)
    lease = db_session.get(Lease, UUID(envelope["lease"]["lease_id"]))
    assert lease is not None
    before_heartbeat = lease.heartbeat_at
    before_events = db_session.scalar(select(func.count()).select_from(Event))
    response = api_client.post(
        f"/api/v1/worker/{action}", json=body, headers=_worker(authenticated_lane)
    )
    assert response.status_code == 403, response.text
    db_session.expire_all()
    task = db_session.get(Task, UUID(task_id))
    assert task is not None
    assert task.status.value == ("running" if action == "result" else "leased")
    assert lease.heartbeat_at == before_heartbeat
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    assert db_session.scalar(select(func.count()).select_from(Event)) == before_events


@pytest.mark.parametrize("config", ["missing", "duplicate", "owner_collision"])
def test_http_worker_authentication_fails_closed_on_configuration_errors(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, config: str
) -> None:
    headers = _worker()
    if config == "missing":
        for lane in ("OLLAMA", "CLAUDE", "CODEX"):
            monkeypatch.delenv(f"DECK_WORKER_TOKEN_{lane}")
        monkeypatch.setenv("DECK_WORKER_TOKEN", "synthetic-legacy")
    elif config == "duplicate":
        monkeypatch.setenv("DECK_WORKER_TOKEN_CLAUDE", headers["Authorization"].split(" ", 1)[1])
    else:
        monkeypatch.setenv("DECK_OWNER_TOKEN", headers["Authorization"].split(" ", 1)[1])
    response = api_client.post("/api/v1/worker/lease", json={"worker": "ollama"}, headers=headers)
    assert response.status_code == 503
    assert "test-worker-token" not in response.text


def test_worker_headers_cannot_select_conflicting_lanes(api_client: TestClient) -> None:
    headers = _worker()
    headers["X-Deck-Worker-Token"] = _worker("claude")["Authorization"].split(" ", 1)[1]
    response = api_client.post("/api/v1/worker/lease", json={"worker": "ollama"}, headers=headers)
    assert response.status_code == 401


def test_worker_credential_collision_never_grants_owner_access(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _worker("claude")
    monkeypatch.setenv("DECK_OWNER_TOKEN", headers["Authorization"].split(" ", 1)[1])
    response = api_client.get("/api/v1/tasks", headers=headers)
    assert response.status_code == 503
    assert "test-claude-token" not in response.text
