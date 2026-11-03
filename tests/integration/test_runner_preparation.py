"""Approved exact bytes become a safe committed transport, or no transport at all."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Lease, RedactionMap, Task
from app.db.queue import dequeue_next_queued_task, reap_expired_leases
from app.db.repository import approve_task, create_task_from_v1
from app.db.state_machine import TaskState
from app.privacy import gate
from app.privacy.files import FileInputError
from app.redaction.artifacts import PayloadArtifactError
from app.workers import preparation
from app.workers.preparation import claim_prepared_attempt, prepare_claimed_task
from tests.integration.test_database import _minimal_task_payload


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECK_INBOX_DIR", str(inbox))
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    return inbox, tmp_path / "runs"


def _approved(session: Session, *, file: bool = False, cloud: bool = False) -> Task:
    payload = _minimal_task_payload(uuid4())
    if cloud:
        payload.update(
            data_class="cloud_redacted",
            instructions="Contact synthetic" + "@" + "example.invalid",
            fallback_chain=["claude"],
        )
    if file:
        payload["inputs"] = [{"type": "file", "name": "brief", "path": "brief.txt"}]
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    assert task.status == TaskState.AWAITING_APPROVAL
    approve_task(session, task)
    assert task.status == TaskState.QUEUED
    session.commit()
    return task


def _claim(session: Session, runs: Path, worker: Worker = Worker.OLLAMA):
    return claim_prepared_attempt(
        session, worker=worker, lease_ttl=timedelta(seconds=60), runs_root=runs
    )


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_cloud_transport_excludes_originals_maps_and_audit_values(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    task = _approved(db_session, cloud=True)
    prepared = _claim(db_session, runs, Worker.CLAUDE)
    assert prepared is not None
    assert prepared.lane == Worker.CLAUDE
    assert prepared.attempt == 1
    original = "synthetic" + "@" + "example.invalid"
    assert original not in prepared.payload_text
    assert original not in repr(prepared)
    assert not {"payload", "mapping", "original", "events", "results"}.intersection(
        asdict(prepared)
    )
    data = (runs / prepared.artifact_path).read_bytes()
    assert data == prepared.payload_text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == prepared.payload_sha256
    event = db_session.scalar(select(Event).where(Event.event_type == "task.prepare"))
    assert event is not None
    assert event.task_id == task.id
    assert event.attempt == prepared.attempt and event.lease_id == prepared.lease_id
    assert event.detail["payload_sha256"] == prepared.payload_sha256
    assert event.detail["redaction_placeholders"] == prepared.redaction_placeholders == 1
    assert original not in json.dumps(event.detail)
    factory = sessionmaker(bind=db_session.get_bind())
    with factory() as observer:
        assert _count(observer, RedactionMap) == 0
        assert observer.scalar(select(Task.status).where(Task.id == task.id)) == TaskState.QUEUED
    db_session.commit()
    with factory() as observer:
        assert _count(observer, RedactionMap) == 1
        assert observer.scalar(select(Task.status).where(Task.id == task.id)) == TaskState.LEASED


def test_changed_approved_attachment_blocks_without_transport_or_new_maps(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    inbox, runs = paths
    (inbox / "brief.txt").write_text("approved text", encoding="utf-8")
    task = _approved(db_session, file=True, cloud=True)
    (inbox / "brief.txt").write_text("changed ordinary text", encoding="utf-8")
    assert _claim(db_session, runs, Worker.CLAUDE) is None
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.BLOCKED and task.attempt == 1
    assert _count(db_session, RedactionMap) == 0
    assert _count(db_session, Lease) == 0
    assert not runs.exists()
    event = db_session.scalar(select(Event).where(Event.event_type == "task.block"))
    assert event is not None and event.detail["code"] == "approval_hash_mismatch"
    assert "brief.txt" not in json.dumps(event.detail)


def test_missing_approval_digest_metadata_cannot_prepare_a_legacy_claim(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    payload = _minimal_task_payload(uuid4())
    task = create_task_from_v1(db_session, TaskV1.model_validate(payload))
    task.status = TaskState.QUEUED
    task.approved_at = db_session.scalar(select(func.clock_timestamp()))
    db_session.commit()
    assert _claim(db_session, runs) is None
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.BLOCKED
    event = db_session.scalar(select(Event).where(Event.event_type == "task.block"))
    assert event.detail["code"] == "approval_hashes_unavailable"
    assert not runs.exists()


def test_final_read_is_inspected_hashed_and_sent_without_reopening(
    db_session: Session, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox, runs = paths
    path = inbox / "brief.txt"
    original = "\ufeffapproved bytes\r\n"
    path.write_bytes(original.encode("utf-8"))
    _approved(db_session, file=True)
    actual = gate.read_text_input
    calls = []

    def read(*args, **kwargs):
        text = actual(*args, **kwargs)
        calls.append(text)
        if len(calls) == 2:
            path.write_text("changed after final inspection", encoding="utf-8")
        return text

    monkeypatch.setattr(gate, "read_text_input", read)
    prepared = _claim(db_session, runs)
    assert prepared is not None
    assert calls == [original, original]  # Queue recheck, then the authoritative builder read.
    assert original in prepared.payload_text
    assert "changed after final inspection" not in prepared.payload_text
    assert (runs / prepared.artifact_path).read_bytes() == prepared.payload_text.encode("utf-8")


def test_file_disappearing_after_poll_reverts_tentative_claim_and_remains_retryable(
    db_session: Session, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox, runs = paths
    (inbox / "brief.txt").write_text("approved bytes", encoding="utf-8")
    task = _approved(db_session, file=True)
    actual = gate.read_text_input
    calls = 0

    def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FileInputError("file_unavailable")
        return actual(*args, **kwargs)

    monkeypatch.setattr(gate, "read_text_input", read)
    assert _claim(db_session, runs) is None
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert _count(db_session, Lease) == 0
    assert db_session.scalar(select(Event).where(Event.event_type == "task.acquire")) is None
    event = db_session.scalar(select(Event).where(Event.event_type == "task.gate_error"))
    assert event is not None and event.detail["code"] == "file_unavailable"
    assert not runs.exists()


def test_failed_artifact_publication_rolls_back_map_and_claim(
    db_session: Session, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runs = paths
    task = _approved(db_session, cloud=True)

    def fail(*args, **kwargs):
        raise PayloadArtifactError("synthetic_artifact_failure")

    monkeypatch.setattr(preparation, "write_payload_sent", fail)
    with pytest.raises(PayloadArtifactError):
        _claim(db_session, runs, Worker.CLAUDE)
    db_session.rollback()
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert _count(db_session, RedactionMap) == _count(db_session, Lease) == 0
    assert db_session.scalar(select(Event).where(Event.event_type == "task.prepare")) is None


def test_rollback_leaves_only_an_unsent_immutable_artifact(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    task = _approved(db_session, cloud=True)
    prepared = _claim(db_session, runs, Worker.CLAUDE)
    assert prepared is not None
    db_session.rollback()
    db_session.refresh(task)
    assert task.status == TaskState.QUEUED and task.attempt == 0
    assert _count(db_session, RedactionMap) == _count(db_session, Lease) == 0
    assert db_session.scalar(select(Event).where(Event.event_type == "task.prepare")) is None
    assert (runs / prepared.artifact_path).is_file()


def test_retry_reuses_stable_maps_and_creates_a_new_attempt_artifact(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    task = _approved(db_session, cloud=True)
    first = _claim(db_session, runs, Worker.CLAUDE)
    assert first is not None
    db_session.commit()
    identities = set(db_session.scalars(select(RedactionMap.id)))
    lease = db_session.scalar(select(Lease).where(Lease.task_id == task.id))
    lease.expires_at -= timedelta(minutes=5)
    db_session.commit()
    assert reap_expired_leases(db_session) == 1
    db_session.commit()
    second = _claim(db_session, runs, Worker.CLAUDE)
    assert second is not None and second.attempt == 2
    assert second.payload_text == first.payload_text
    assert second.payload_sha256 == first.payload_sha256
    assert set(db_session.scalars(select(RedactionMap.id))) == identities
    assert second.artifact_path != first.artifact_path


def test_prepare_requires_a_current_claim_before_allocating(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    task = _approved(db_session, cloud=True)
    with pytest.raises(ValueError):
        prepare_claimed_task(db_session, task, runs_root=runs)
    assert _count(db_session, RedactionMap) == 0
    assert not runs.exists()


def test_artifact_conflict_cannot_replace_a_previous_attempt_payload(
    db_session: Session, paths: tuple[Path, Path]
) -> None:
    _, runs = paths
    task = _approved(db_session)
    claim = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(seconds=60)
    )
    assert claim is not None
    from app.redaction.artifacts import write_payload_sent

    write_payload_sent(
        runs, task_id=task.id, attempt=task.attempt, text="unsent incompatible bytes"
    )
    with pytest.raises(PayloadArtifactError):
        prepare_claimed_task(db_session, task, runs_root=runs)
    assert db_session.scalar(select(Event).where(Event.event_type == "task.prepare")) is None
    assert (runs / str(task.id) / str(task.attempt) / "payload.sent.md").read_text() == (
        "unsent incompatible bytes"
    )


def test_deadline_crossing_during_artifact_write_commits_expiry_without_transport(
    db_session: Session, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    _, runs = paths
    task = _approved(db_session, cloud=True)
    task.deadline_at = db_session.scalar(select(func.clock_timestamp())) + timedelta(seconds=0.4)
    db_session.commit()
    actual = preparation.write_payload_sent

    def slow_write(*args, **kwargs):
        artifact = actual(*args, **kwargs)
        time.sleep(0.5)
        return artifact

    monkeypatch.setattr(preparation, "write_payload_sent", slow_write)
    assert _claim(db_session, runs, Worker.CLAUDE) is None
    db_session.commit()
    db_session.refresh(task)
    assert task.status == TaskState.EXPIRED
    assert _count(db_session, RedactionMap) == _count(db_session, Lease) == 0
    assert db_session.scalar(select(Event).where(Event.event_type == "task.prepare")) is None
    assert db_session.scalar(select(Event).where(Event.event_type == "task.expire")) is not None
