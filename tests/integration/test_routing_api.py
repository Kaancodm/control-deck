"""Owner-only routing previews never persist or dispatch work."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.db.models import Event, Lease, RedactionMap, Result, Task
from app.routing.config import RouterConfig
from tests.integration.test_data_gate import (
    _OWNER,
    _WORKER,
)
from tests.integration.test_data_gate import (
    gate_client as gate_client,
)
from tests.integration.test_data_gate import (
    privacy_paths as privacy_paths,
)
from tests.integration.test_database import _minimal_task_payload


def _payload():
    data = _minimal_task_payload(uuid.uuid4())
    data["instructions"] = "Ordinary preview text"
    return data


def _counts(session):
    return {
        table.__tablename__: session.scalar(select(func.count()).select_from(table))
        for table in (Task, Event, Result, Lease, RedactionMap)
    }


def test_preview_ready_does_not_write_or_create_artifacts(
    gate_client, db_session, tmp_path, monkeypatch
):
    root = tmp_path / "preview-runs"
    monkeypatch.setenv("DECK_RUNS_DIR", str(root))
    before = _counts(db_session)
    writes = []

    def detect_write(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE")):
            writes.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", detect_write)
    try:
        response = gate_client.post("/api/v1/tasks/dry-run", json=_payload(), headers=_OWNER)
    finally:
        event.remove(bind, "before_cursor_execute", detect_write)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["gate_status"] == "clear"
    assert body["plan"]["chain"] == ["ollama"]
    assert body["budgets"]["ollama"]["used"] == 0
    assert body["budgets"]["ollama"]["remaining"] is None
    assert "Ordinary preview text" not in response.text
    assert not writes
    assert _counts(db_session) == before
    assert not root.exists()


@pytest.mark.parametrize("headers", [{}, _WORKER])
def test_preview_requires_owner(gate_client, db_session, headers):
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=_payload(), headers=headers)
    assert response.status_code == 401
    assert _counts(db_session) == before


@pytest.mark.parametrize(
    "problem",
    ["missing_data_class", "unknown_field", "past_deadline", "naive_deadline", "numeric_deadline"],
)
def test_invalid_preview_has_no_writes_or_payload_echo(gate_client, db_session, problem):
    payload = _payload()
    marker = "untrusted-input-marker"
    if problem == "missing_data_class":
        payload.pop("data_class")
    elif problem == "unknown_field":
        payload[marker] = marker
    elif problem == "past_deadline":
        payload["deadline_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    elif problem == "naive_deadline":
        payload["deadline_at"] = "2099-01-01T00:00:00"
    else:
        payload["deadline_at"] = 123
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=payload, headers=_OWNER)
    assert response.status_code == 422, response.text
    assert marker not in response.text
    assert _counts(db_session) == before


def test_preview_accepts_future_owner_deadline_without_persisting(gate_client, db_session):
    payload = _payload()
    payload["deadline_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=payload, headers=_OWNER)
    assert response.status_code == 200, response.text
    assert _counts(db_session) == before


@pytest.mark.parametrize("data_class", ["local_only", "cloud_ok", "cloud_redacted"])
def test_preview_secret_is_rejected_without_echo_or_storage(gate_client, db_session, data_class):
    payload = _payload()
    payload["data_class"] = data_class
    credential = "DECK_OWNER_TOKEN=" + "synthetic-preview-value"
    payload["instructions"] = credential
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=payload, headers=_OWNER)
    assert response.status_code == 422, response.text
    assert "secret_detected" in response.text
    assert credential not in response.text
    assert "synthetic-preview-value" not in response.text
    assert _counts(db_session) == before


@pytest.mark.parametrize("data_class", ["cloud_ok", "cloud_redacted"])
def test_preview_reports_current_cloud_gate_without_allocating_map(
    gate_client, db_session, data_class
):
    payload = _payload()
    payload["data_class"] = data_class
    marker = "fixture" + "@" + "example.invalid"
    payload["instructions"] = marker
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=payload, headers=_OWNER)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert response.json()["gate_status"] == "blocked"
    assert response.json()["reason"] == "privacy_gate"
    assert response.json()["findings"]
    assert marker not in response.text
    assert _counts(db_session) == before


def test_preview_budget_exhaustion_is_deferred(gate_client, db_session, monkeypatch):
    config = RouterConfig.model_validate(
        {"lanes": {worker: {"daily_attempt_limit": 0} for worker in ("ollama", "claude", "codex")}}
    )
    monkeypatch.setattr("app.api.routes.tasks.get_router_config", lambda: config)
    before = _counts(db_session)
    response = gate_client.post("/api/v1/tasks/dry-run", json=_payload(), headers=_OWNER)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deferred"
    assert response.json()["reason"] == "budget_exhausted"
    assert response.json()["budgets"]["ollama"] == {"used": 0, "limit": 0, "remaining": 0}
    assert _counts(db_session) == before


@pytest.mark.parametrize("endpoint", ["/api/v1/tasks/dry-run", "/api/v1/tasks"])
def test_invalid_config_returns_503_without_partial_persistence(
    gate_client, db_session, tmp_path, monkeypatch, endpoint
):
    missing = tmp_path / "missing-router.yaml"
    monkeypatch.setenv("DECK_ROUTER_FILE", str(missing))
    before = _counts(db_session)
    response = gate_client.post(endpoint, json=_payload(), headers=_OWNER)
    assert response.status_code == 503, response.text
    assert str(missing) not in response.text
    assert _counts(db_session) == before


def test_preview_does_not_sweep_existing_overdue_tasks(gate_client, db_session):
    payload = _payload()
    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)
    assert response.status_code == 201
    stored = db_session.get(Task, uuid.UUID(payload["task_id"]))
    assert stored is not None
    stored.deadline_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    before = _counts(db_session)
    preview = gate_client.post("/api/v1/tasks/dry-run", json=_payload(), headers=_OWNER)
    assert preview.status_code == 200
    db_session.refresh(stored)
    assert stored.status.value == "awaiting_approval"
    assert _counts(db_session) == before
