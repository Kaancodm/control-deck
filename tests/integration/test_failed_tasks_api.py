"""Owner-only failure listing remains read-only and exposes only safe metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.core.schemas import Worker
from app.db.failures import list_failed_tasks
from app.db.models import Event, Task
from app.db.state_machine import TaskState
from app.db.worker_ops import submit_worker_result
from tests.integration.test_data_gate import _OWNER, _WORKER
from tests.integration.test_data_gate import gate_client as gate_client
from tests.integration.test_data_gate import privacy_paths as privacy_paths
from tests.integration.test_failure_policy import _claim, _fail, _queued


def test_failed_listing_reads_safe_metadata_without_writes_or_deadline_sweep(
    gate_client, db_session
):
    task_id = _queued(db_session, max_attempts=1)
    _, lease_id, result = _claim(db_session, Worker.CLAUDE)
    _fail(db_session, lease_id, result, "timeout")
    waiting_id = _queued(db_session)
    waiting = db_session.get(Task, waiting_id)
    waiting.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    before = db_session.scalar(select(func.count()).select_from(Event))
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement.upper())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        response = gate_client.get("/api/v1/tasks/failed", headers=_OWNER)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1 and body["limit"] == 50 and body["offset"] == 0
    row = body["items"][0]
    assert row["id"] == str(task_id) and row["status"] == "failed"
    assert row["last_error_code"] == "timeout"
    assert row["lane"] == "claude" and row["next_attempt_at"] is None
    assert row["retry_state"]["pinned_worker"] == "claude"
    assert "Synthetic provider failure" not in response.text
    assert "payload" not in row and "output" not in row
    assert not any("FOR UPDATE" in statement for statement in statements)
    assert not any(
        statement.lstrip().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
        for statement in statements
    )
    assert db_session.get(Task, waiting_id).status == TaskState.QUEUED
    assert db_session.scalar(select(func.count()).select_from(Event)) == before


def test_listing_uses_effective_core_acceptance_error_and_never_revives(gate_client, db_session):
    task_id = _queued(
        db_session, max_attempts=1, acceptance=[{"type": "must_contain", "value": "required"}]
    )
    _, lease_id, result = _claim(db_session, Worker.CLAUDE)
    assert result["status"] == "succeeded" and result["error"] is None
    submit_worker_result(db_session, lease_id=lease_id, result_payload=result)
    db_session.commit()
    response = gate_client.get("/api/v1/tasks/failed", headers=_OWNER)
    assert response.json()["items"][0]["last_error_code"] == "acceptance_failed"
    assert gate_client.post(f"/api/v1/tasks/{task_id}/approve", headers=_OWNER).status_code == 409
    assert gate_client.post(f"/api/v1/tasks/{task_id}/retry", headers=_OWNER).status_code == 404
    assert db_session.get(Task, task_id).status == TaskState.FAILED


def test_failed_listing_filters_and_pages_only_failed_tasks(gate_client, db_session):
    ids = []
    for local_only in (False, True, True):
        ids.append(_queued(db_session, local_only=local_only, max_attempts=1))
        _, lease_id, result = _claim(db_session, Worker.OLLAMA if local_only else Worker.CLAUDE)
        _fail(db_session, lease_id, result, "timeout")
    first = gate_client.get(
        "/api/v1/tasks/failed", headers=_OWNER, params={"data_class": "local_only", "limit": 1}
    ).json()
    second = gate_client.get(
        "/api/v1/tasks/failed",
        headers=_OWNER,
        params={"data_class": "local_only", "limit": 1, "offset": 1},
    ).json()
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == set(map(str, ids[1:]))


@pytest.mark.parametrize("headers", [{}, _WORKER])
def test_failed_listing_requires_owner(gate_client, headers):
    assert gate_client.get("/api/v1/tasks/failed", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"data_class": "unknown"}]
)
def test_failed_listing_rejects_invalid_filters(gate_client, params):
    assert gate_client.get("/api/v1/tasks/failed", headers=_OWNER, params=params).status_code == 422


@pytest.mark.parametrize("kwargs", [{"limit": True}, {"offset": -1}, {"data_class": "local_only"}])
def test_failed_listing_service_validates_non_http_callers(db_session, kwargs):
    with pytest.raises(ValueError):
        list_failed_tasks(db_session, **kwargs)
