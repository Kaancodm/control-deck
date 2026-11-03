"""Approval digests and retryable attachment failures against PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Task
from app.db.state_machine import TaskState
from app.privacy import gate
from tests.integration.test_data_gate import _OWNER, _WORKER, _create, _payload
from tests.integration.test_data_gate import gate_client as gate_client
from tests.integration.test_data_gate import privacy_paths as privacy_paths


def _file_payload() -> dict:
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": "input.txt"}]
    return payload


def _approve(client: TestClient, task_id: str):
    return client.post(f"/api/v1/tasks/{task_id}/approve", headers=_OWNER)


def test_approval_hashes_the_scanned_read_without_reopening_attachment(
    gate_client: TestClient,
    privacy_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_bytes(b"initial text")
    payload = _file_payload()
    _create(gate_client, payload)
    approved_bytes = "\ufeffGrüezi\r\nnew input\n".encode()
    path.write_bytes(approved_bytes)
    reads = []
    original_read = gate.read_text_input

    def read_once(*args, **kwargs):
        content = original_read(*args, **kwargs)
        reads.append(content)
        path.write_bytes(b"changed after inspection")
        return content

    monkeypatch.setattr(gate, "read_text_input", read_once)
    response = _approve(gate_client, payload["task_id"])

    assert response.status_code == 200, response.text
    assert len(reads) == 1
    event = next(item for item in response.json()["events"] if item["event_type"] == "task.approve")
    assert event["detail"]["file_hashes"] == [
        {"input_index": 0, "sha256": hashlib.sha256(approved_bytes).hexdigest()}
    ]
    assert str(path) not in json.dumps(event)
    assert "Grüezi" not in json.dumps(event, ensure_ascii=False)


def test_missing_file_during_approval_keeps_task_retryable(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_bytes(b"ordinary input")
    payload = _file_payload()
    _create(gate_client, payload)
    path.unlink()

    response = _approve(gate_client, payload["task_id"])

    assert response.status_code == 409, response.text
    assert "file_unavailable" in response.text
    assert str(path) not in response.text
    db_session.expire_all()
    task = db_session.get(Task, uuid.UUID(payload["task_id"]))
    assert task is not None
    assert task.status == TaskState.AWAITING_APPROVAL
    assert task.approved_at is None and task.attempt == 0 and task.lease is None
    failure = next(event for event in task.events if event.event_type == "task.gate_error")
    assert failure.from_status == failure.to_status == TaskState.AWAITING_APPROVAL
    assert failure.detail["code"] == "file_unavailable"
    assert "file_hashes" not in failure.detail
    assert not any(event.event_type == "task.approve" for event in task.events)

    path.write_bytes(b"restored input")
    retried = _approve(gate_client, payload["task_id"])
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"


def test_missing_file_in_queue_skips_to_clean_task_then_can_be_retried(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_bytes(b"ordinary input")
    unavailable = _file_payload()
    clean = _payload()
    for payload in (unavailable, clean):
        _create(gate_client, payload)
        assert _approve(gate_client, payload["task_id"]).status_code == 200
    path.unlink()

    response = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["id"] == clean["task_id"]
    db_session.expire_all()
    task = db_session.get(Task, uuid.UUID(unavailable["task_id"]))
    assert task is not None and task.status == TaskState.QUEUED
    assert task.attempt == 0 and task.lease is None
    failures = [event for event in task.events if event.event_type == "task.gate_error"]
    assert len(failures) == 1
    assert failures[0].detail["code"] == "file_unavailable"
    assert str(path) not in json.dumps(failures[0].detail)

    path.write_bytes(b"ordinary input")
    retried = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)
    assert retried.status_code == 200, retried.text
    assert retried.json()["task"]["id"] == unavailable["task_id"]


def test_only_missing_file_in_queue_returns_without_rechecking_forever(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_bytes(b"ordinary input")
    payload = _file_payload()
    _create(gate_client, payload)
    assert _approve(gate_client, payload["task_id"]).status_code == 200
    path.unlink()

    for attempt in range(1, 3):
        response = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)
        assert response.status_code == 404, response.text
        db_session.expire_all()
        task = db_session.get(Task, uuid.UUID(payload["task_id"]))
        assert task is not None and task.status == TaskState.QUEUED
        assert task.attempt == 0 and task.lease is None
        assert sum(event.event_type == "task.gate_error" for event in task.events) == attempt


@pytest.mark.parametrize("change", ["binary", "symlink"])
def test_deterministic_file_rejections_remain_terminal(
    gate_client: TestClient, privacy_paths: tuple[Path, Path], change: str
) -> None:
    path = privacy_paths[0] / "input.txt"
    path.write_bytes(b"ordinary input")
    payload = _file_payload()
    _create(gate_client, payload)
    if change == "binary":
        path.write_bytes(b"binary\x00input")
    else:
        outside = privacy_paths[0].parent / "outside.txt"
        outside.write_bytes(b"ordinary input")
        path.unlink()
        path.symlink_to(outside)

    response = _approve(gate_client, payload["task_id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert not any(event["event_type"] == "task.approve" for event in response.json()["events"])
