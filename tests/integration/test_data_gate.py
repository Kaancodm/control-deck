"""Privacy-gate API behavior against a real, isolated database."""

from __future__ import annotations

import json
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.core.schemas import DataClass, TaskV1
from app.db.models import Event, Lease, Task
from app.db.repository import create_task_from_v1
from app.db.state_machine import TaskState
from tests.integration.test_database import _minimal_task_payload

_OWNER = {"Authorization": "Bearer test-gate-owner"}
_WORKER = {"Authorization": "Bearer test-gate-worker"}


def _secret() -> str:
    return "sk-" + "TEST-" + "SYNTHETIC-NOT-A-CREDENTIAL"


def _email() -> str:
    return "fixture" + "@" + "example.invalid"


def _payload(data_class: str = "local_only") -> dict[str, Any]:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["data_class"] = data_class
    return payload


@pytest.fixture
def privacy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    blocklist = tmp_path / "private-blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_INBOX_DIR", str(inbox))
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    return inbox, blocklist


@pytest.fixture
def gate_client(
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "test-gate-owner")
    monkeypatch.setenv("DECK_WORKER_TOKEN", "test-gate-worker")
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


def _assert_nothing_persisted(session: Session) -> None:
    assert session.scalar(select(func.count()).select_from(Task)) == 0
    assert session.scalar(select(func.count()).select_from(Event)) == 0
    assert session.scalar(select(func.count()).select_from(Lease)) == 0


def _create(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/tasks", json=payload, headers=_OWNER)
    assert response.status_code == 201, response.text
    return response.json()["task"]


@pytest.mark.parametrize("data_class", ["local_only", "cloud_ok", "cloud_redacted"])
def test_secret_is_rejected_before_any_persistence(
    gate_client: TestClient, db_session: Session, data_class: str
) -> None:
    payload = _payload(data_class)
    payload["instructions"] = _secret()

    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)

    assert response.status_code == 422, response.text
    assert _secret() not in response.text
    _assert_nothing_persisted(db_session)


@pytest.mark.parametrize(
    "field", ["title", "name", "value", "mime", "path", "acceptance", "label_key", "label_value"]
)
def test_secret_in_metadata_is_rejected_without_echo(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    field: str,
) -> None:
    payload = _payload()
    if field == "title":
        payload["title"] = _secret()
    elif field in {"name", "value", "mime"}:
        payload["inputs"] = [{"type": "text", "name": "brief", "value": "ordinary text"}]
        payload["inputs"][0][field] = _secret()
    elif field == "path":
        name = _secret() + ".txt"
        (privacy_paths[0] / name).write_text("ordinary text", encoding="utf-8")
        payload["inputs"] = [{"type": "file", "name": "brief", "path": name}]
    elif field == "acceptance":
        payload["acceptance"] = [{"type": "must_contain", "value": _secret()}]
    elif field == "label_key":
        payload["labels"] = {_secret(): "ordinary text"}
    else:
        payload["labels"] = {"note": _secret()}

    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)

    assert response.status_code == 422, response.text
    assert _secret() not in response.text
    _assert_nothing_persisted(db_session)


@pytest.mark.parametrize("shape", ["unknown_value", "unknown_key", "wrong_type", "non_object"])
def test_invalid_request_does_not_echo_secret_before_gate(
    gate_client: TestClient, db_session: Session, shape: str
) -> None:
    payload: Any = _payload()
    if shape == "unknown_value":
        payload["unexpected"] = _secret()
    elif shape == "unknown_key":
        payload[_secret()] = "unexpected"
    elif shape == "wrong_type":
        payload["instructions"] = {"nested": _secret()}
    else:
        payload = [_secret()]

    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)

    assert response.status_code == 422, response.text
    assert _secret() not in response.text
    _assert_nothing_persisted(db_session)


@pytest.mark.parametrize("data_class", ["cloud_ok", "cloud_redacted"])
@pytest.mark.parametrize("finding", ["email", "blocklist"])
def test_cloud_findings_block_with_safe_audit_metadata(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    data_class: str,
    finding: str,
) -> None:
    marker = _email() if finding == "email" else "Restricted Fixture Phrase"
    if finding == "blocklist":
        privacy_paths[1].write_text(marker + "\n", encoding="utf-8")
    payload = _payload(data_class)
    payload["instructions"] = marker

    created = _create(gate_client, payload)

    assert created["status"] == "blocked"
    assert created["approved_at"] is None
    events = created["events"]
    assert events[-1]["event_type"] == "task.block"
    assert marker not in json.dumps(events)
    assert events[-1]["detail"]["findings"]
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    approval = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)
    assert approval.status_code == 409


def test_local_personal_data_is_allowed_without_cloud_configuration(
    gate_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DECK_BLOCKLIST_PATH")
    payload = _payload()
    payload["instructions"] = _email()
    assert _create(gate_client, payload)["status"] == "awaiting_approval"


@pytest.mark.parametrize("data_class", ["local_only", "cloud_ok", "cloud_redacted"])
def test_clean_task_waits_for_owner_approval(gate_client: TestClient, data_class: str) -> None:
    created = _create(gate_client, _payload(data_class))
    assert created["status"] == "awaiting_approval"
    assert created["approved_at"] is None


@pytest.mark.parametrize("problem", ["missing_setting", "missing_file", "invalid_utf8"])
def test_cloud_configuration_error_fails_closed_without_persistence(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    blocklist = privacy_paths[1]
    if problem == "missing_setting":
        monkeypatch.delenv("DECK_BLOCKLIST_PATH")
    elif problem == "missing_file":
        blocklist.unlink()
    else:
        blocklist.write_bytes(b"\xff")

    response = gate_client.post("/api/v1/tasks", json=_payload("cloud_ok"), headers=_OWNER)

    assert response.status_code == 503, response.text
    assert str(blocklist) not in response.text
    _assert_nothing_persisted(db_session)


@pytest.mark.parametrize("extension", ["txt", "md", "json", "csv"])
def test_supported_text_attachment_is_scanned_and_accepted(
    gate_client: TestClient, privacy_paths: tuple[Path, Path], extension: str
) -> None:
    name = f"brief.{extension}"
    (privacy_paths[0] / name).write_text("ordinary input", encoding="utf-8")
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": name}]
    assert _create(gate_client, payload)["status"] == "awaiting_approval"


@pytest.mark.parametrize("problem", ["missing", "symlink", "binary", "oversized", "unsupported"])
def test_invalid_attachment_is_rejected_without_host_path_disclosure(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    problem: str,
) -> None:
    inbox = privacy_paths[0]
    name = "brief.pdf" if problem == "unsupported" else "brief.txt"
    target = inbox / name
    if problem == "symlink":
        outside = inbox.parent / "outside.txt"
        outside.write_text("ordinary text", encoding="utf-8")
        target.symlink_to(outside)
    elif problem == "binary":
        target.write_bytes(b"binary\x00input")
    elif problem == "oversized":
        target.write_bytes(b"x" * (1024 * 1024 + 1))
    elif problem == "unsupported":
        target.write_text("ordinary text", encoding="utf-8")
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": name}]

    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)

    assert response.status_code == 422, response.text
    assert str(inbox) not in response.text
    _assert_nothing_persisted(db_session)


@pytest.mark.parametrize("finding", ["secret", "email"])
def test_attachment_content_is_checked_without_exposing_it(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    finding: str,
) -> None:
    marker = _secret() if finding == "secret" else _email()
    (privacy_paths[0] / "brief.txt").write_text(marker, encoding="utf-8")
    payload = _payload("cloud_ok")
    payload["inputs"] = [{"type": "file", "name": "brief", "path": "brief.txt"}]
    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)
    assert marker not in response.text
    if finding == "secret":
        assert response.status_code == 422, response.text
        _assert_nothing_persisted(db_session)
    else:
        assert response.status_code == 201, response.text
        assert response.json()["task"]["status"] == "blocked"


def test_unresolved_context_is_not_silently_accepted(
    gate_client: TestClient, db_session: Session
) -> None:
    payload = _payload()
    payload["context_refs"] = [{"source": "knowledge_db", "ref": "fixture-document"}]
    response = gate_client.post("/api/v1/tasks", json=payload, headers=_OWNER)
    assert response.status_code == 422, response.text
    assert "context_unavailable" in response.text
    _assert_nothing_persisted(db_session)


def test_internal_producer_cannot_bypass_secret_rejection(
    db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    payload = _payload()
    payload["instructions"] = _secret()
    with pytest.raises(ValueError) as rejected:
        create_task_from_v1(db_session, TaskV1.model_validate(payload))
    assert _secret() not in str(rejected.value)
    _assert_nothing_persisted(db_session)


def test_approval_rechecks_changed_attachment(
    gate_client: TestClient, db_session: Session, privacy_paths: tuple[Path, Path]
) -> None:
    attachment = privacy_paths[0] / "brief.txt"
    attachment.write_text("ordinary input", encoding="utf-8")
    payload = _payload()
    payload["inputs"] = [{"type": "file", "name": "brief", "path": "brief.txt"}]
    _create(gate_client, payload)
    attachment.write_text(_secret(), encoding="utf-8")

    response = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert response.json()["approved_at"] is None
    assert _secret() not in response.text
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


@pytest.mark.parametrize("change", ["attachment", "blocklist"])
def test_lease_rechecks_changes_and_continues_to_clean_task(
    gate_client: TestClient,
    db_session: Session,
    privacy_paths: tuple[Path, Path],
    change: str,
) -> None:
    inbox, blocklist = privacy_paths
    marker = "Restricted Fixture Phrase"
    payload = _payload("cloud_ok")
    if change == "attachment":
        (inbox / "brief.txt").write_text("ordinary text", encoding="utf-8")
        payload["inputs"] = [{"type": "file", "name": "brief", "path": "brief.txt"}]
    else:
        payload["instructions"] = marker
    clean = _payload()
    for item in (payload, clean):
        _create(gate_client, item)
        response = gate_client.post(f"/api/v1/tasks/{item['task_id']}/approve", headers=_OWNER)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
    if change == "attachment":
        (inbox / "brief.txt").write_text(_secret(), encoding="utf-8")
    else:
        blocklist.write_text(marker + "\n", encoding="utf-8")

    response = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["id"] == clean["task_id"]
    db_session.expire_all()
    blocked = db_session.get(Task, uuid.UUID(payload["task_id"]))
    assert blocked is not None and blocked.status.value == "blocked"
    assert blocked.attempt == 0 and blocked.lease is None
    audit = json.dumps([event.detail for event in blocked.events])
    assert marker not in audit and _secret() not in audit
    assert any(event.event_type == "task.block" for event in blocked.events)


@pytest.mark.parametrize("action", ["approve", "lease"])
def test_missing_configuration_does_not_approve_or_lease_cloud_task(
    gate_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    payload = _payload("cloud_ok")
    _create(gate_client, payload)
    if action == "lease":
        approved = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)
        assert approved.status_code == 200
    monkeypatch.delenv("DECK_BLOCKLIST_PATH")

    if action == "approve":
        response = gate_client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_OWNER)
    else:
        response = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)

    assert response.status_code == 503, response.text
    db_session.expire_all()
    task = db_session.get(Task, uuid.UUID(payload["task_id"]))
    assert task is not None
    assert task.status.value == ("awaiting_approval" if action == "approve" else "queued")
    assert task.lease is None and task.attempt == 0


def test_malformed_legacy_task_is_blocked_without_stalling_queue(
    gate_client: TestClient, db_session: Session
) -> None:
    task_id = uuid.uuid4()
    legacy = Task(
        id=task_id,
        status=TaskState.QUEUED,
        data_class=DataClass.LOCAL_ONLY,
        payload={
            "schema_version": "task.v1",
            "task_id": str(task_id),
            "data_class": "local_only",
            "fallback_chain": ["ollama"],
            "unexpected": _secret(),
        },
        approved_at=datetime.now(tz=UTC),
    )
    db_session.add(legacy)
    db_session.commit()
    clean = _payload()
    _create(gate_client, clean)
    approved = gate_client.post(f"/api/v1/tasks/{clean['task_id']}/approve", headers=_OWNER)
    assert approved.status_code == 200, approved.text

    response = gate_client.post("/api/v1/worker/lease", json={}, headers=_WORKER)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["id"] == clean["task_id"]
    assert _secret() not in response.text
    db_session.refresh(legacy)
    assert legacy.status == TaskState.BLOCKED
    assert legacy.lease is None and legacy.attempt == 0
    blocked_event = next(event for event in legacy.events if event.event_type == "task.block")
    assert blocked_event.detail["code"] == "invalid_task"
    assert _secret() not in json.dumps(blocked_event.detail)
