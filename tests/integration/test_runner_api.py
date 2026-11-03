"""Safe worker transport and committed owner/runner control across HTTP."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Event, RedactionMap, Task
from app.db.session import get_engine
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


def _owner() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.environ["DECK_OWNER_TOKEN"]}


def _worker() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.environ["DECK_WORKER_TOKEN"]}


def _enqueue(
    client: TestClient, *, data_class: str = "local_only", instructions: str = "Text"
) -> str:
    payload = _minimal_task_payload(uuid4())
    payload.update(data_class=data_class, instructions=instructions)
    if data_class != "local_only":
        payload["fallback_chain"] = ["claude"]
    response = client.post("/api/v1/tasks", json=payload, headers=_owner())
    assert response.status_code == 201, response.text
    task_id = response.json()["task"]["id"]
    approval = client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner())
    assert approval.status_code == 200, approval.text
    assert approval.json()["status"] == "queued"
    return task_id


def _claim(client: TestClient, worker: str = "ollama") -> dict:
    response = client.post("/api/v1/worker/lease", json={"worker": worker}, headers=_worker())
    assert response.status_code == 200, response.text
    return response.json()


def _identity(envelope: dict, worker: str = "ollama") -> dict:
    return {
        "task_id": envelope["task"]["id"],
        "attempt": envelope["task"]["attempt"],
        "lease_id": envelope["lease"]["lease_id"],
        "worker": worker,
    }


def _result(envelope: dict, *, worker: str = "ollama", output: str = "Completed") -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "result.v1",
        "task_id": envelope["task"]["id"],
        "attempt": envelope["task"]["attempt"],
        "worker": worker,
        "status": "succeeded",
        "output": {"text": output, "files": []},
        "checks": [],
        "usage": {"wall_seconds": 0.01},
        "timing": {"started_at": now, "finished_at": now},
        "error": None,
        "redaction": {
            "applied": envelope["redaction_applied"],
            "placeholders": envelope["redaction_placeholders"],
        },
        "payload_hash": envelope["payload_sha256"],
        "log_ref": f"runs/{envelope['task']['id']}/{envelope['task']['attempt']}/{worker}.log",
    }


@pytest.mark.parametrize("body", [{}, {"worker": "unknown"}, {"worker": "ollama", "extra": True}])
def test_claim_requires_explicit_known_lane(api_client: TestClient, body: dict) -> None:
    response = api_client.post("/api/v1/worker/lease", json=body, headers=_worker())
    assert response.status_code == 422


def test_cloud_transport_has_no_raw_task_and_maps_are_committed_before_response(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    original = "synthetic" + "@example.invalid"
    task_id = _enqueue(api_client, data_class="cloud_redacted", instructions=original)
    envelope = _claim(api_client, "claude")
    assert original not in str(envelope)
    assert not {"payload", "events", "results", "instructions"}.intersection(envelope["task"])
    assert envelope["redaction_applied"] is True
    with Session(get_engine(for_tests=True)) as observer:
        assert observer.scalar(select(func.count()).select_from(RedactionMap)) == 1
        assert (
            observer.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "task.prepare")
            )
            == 1
        )
        assert str(observer.get(Task, task_id).id) == task_id


def test_result_requires_explicit_start_and_matching_preparation(api_client: TestClient) -> None:
    _enqueue(api_client)
    envelope = _claim(api_client)
    body = {"lease_id": envelope["lease"]["lease_id"], "result": _result(envelope)}
    assert api_client.post("/api/v1/worker/result", json=body, headers=_worker()).status_code == 422
    started = api_client.post("/api/v1/worker/start", json=_identity(envelope), headers=_worker())
    assert started.status_code == 200
    assert started.json()["action"] == "continue"
    body["result"]["payload_hash"] = "0" * 64
    assert api_client.post("/api/v1/worker/result", json=body, headers=_worker()).status_code == 422
    body["result"] = _result(envelope)
    accepted = api_client.post("/api/v1/worker/result", json=body, headers=_worker())
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["task"]["status"] == "review"


def test_owner_stop_blocks_claims_and_heartbeat_requests_cancel(api_client: TestClient) -> None:
    _enqueue(api_client)
    envelope = _claim(api_client)
    assert (
        api_client.post(
            "/api/v1/worker/start", json=_identity(envelope), headers=_worker()
        ).status_code
        == 200
    )
    stopped = api_client.patch(
        "/api/v1/control/settings", json={"changes": {"stop_requested": True}}, headers=_owner()
    )
    assert stopped.status_code == 200, stopped.text
    control = api_client.post(
        "/api/v1/worker/heartbeat", json=_identity(envelope), headers=_worker()
    )
    assert control.status_code == 200
    assert control.json()["action"] == "cancel"
    assert (
        api_client.post(
            "/api/v1/worker/lease", json={"worker": "ollama"}, headers=_worker()
        ).status_code
        == 404
    )


def test_expired_control_is_committed_even_when_worker_stops(
    api_client: TestClient, db_session: Session
) -> None:
    task_id = _enqueue(api_client)
    envelope = _claim(api_client)
    db_session.execute(
        text("UPDATE tasks SET deadline_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
        {"id": task_id},
    )
    db_session.commit()
    response = api_client.post(
        "/api/v1/worker/heartbeat", json=_identity(envelope), headers=_worker()
    )
    assert response.status_code == 200
    assert response.json()["action"] == "expired"
    with Session(get_engine(for_tests=True)) as observer:
        assert observer.get(Task, task_id).status.value == "expired"
        assert (
            observer.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "task.expire")
            )
            == 1
        )


def test_control_settings_require_owner_and_strict_values(api_client: TestClient) -> None:
    assert (
        api_client.patch(
            "/api/v1/control/settings", json={"changes": {"paused": True}}, headers=_worker()
        ).status_code
        == 401
    )
    for changes in ({"max_parallel": True}, {"paused": "false"}, {"unexpected": True}):
        assert (
            api_client.patch(
                "/api/v1/control/settings", json={"changes": changes}, headers=_owner()
            ).status_code
            == 422
        )
    state = api_client.get("/api/v1/control/settings", headers=_owner()).json()
    assert state["paused"] is False
    assert state["max_parallel"] == 1
