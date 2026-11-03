"""Persisted map allocation, safe transport artifacts, and local rehydration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serializers import task_detail
from app.core.schemas import DataClass, TaskV1, Worker
from app.db.models import RedactionMap, Task
from app.db.state_machine import TaskState
from app.privacy.config import PrivacySettings
from app.privacy.gate import PrivacyInputError
from app.redaction import payload as payload_module
from app.redaction.artifacts import write_payload_sent
from app.redaction.engine import RedactionError
from app.redaction.payload import prepare_task_payload, render_payload
from app.redaction.repository import rehydrate_for_task
from tests.integration.test_database import _minimal_task_payload


def _email(name: str = "synthetic") -> str:
    return name + "@" + "example.invalid"


@pytest.fixture
def settings(tmp_path: Path) -> PrivacySettings:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return PrivacySettings(inbox=inbox, blocklist_configured=True)


def _store_task(session: Session, **changes: object) -> tuple[Task, TaskV1]:
    payload = _minimal_task_payload(uuid4())
    payload.update(data_class="cloud_redacted", instructions=_email())
    payload.update(changes)
    model = TaskV1.model_validate(payload)
    row = Task(
        id=model.task_id,
        status=TaskState.AWAITING_APPROVAL,
        data_class=DataClass(model.data_class),
        payload=model.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return row, model


def _map_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(RedactionMap)) or 0


def test_persisted_payload_roundtrip_retry_and_artifact_are_consistent(
    db_session: Session, settings: PrivacySettings, tmp_path: Path
) -> None:
    content = "\ufeff" + _email("attachment") + "\r\n"
    (settings.inbox / "brief.txt").write_bytes(content.encode("utf-8"))
    row, task = _store_task(
        db_session,
        inputs=[{"type": "file", "name": "brief", "path": "brief.txt"}],
        labels={"contact": _email()},
    )
    db_session.commit()
    original = render_payload(task, settings=settings)

    first = prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=1, settings=settings)
    db_session.commit()
    identities = {entry.placeholder: entry.id for entry in db_session.scalars(select(RedactionMap))}
    second = prepare_task_payload(db_session, task, lane=Worker.CODEX, attempt=2, settings=settings)
    db_session.commit()

    assert first == second
    assert first.placeholders == 2
    assert rehydrate_for_task(db_session, task_id=row.id, text=second.text) == original
    assert {
        entry.placeholder: entry.id for entry in db_session.scalars(select(RedactionMap))
    } == identities
    assert _map_count(db_session) == 2
    assert _email() not in first.text and _email("attachment") not in first.text
    artifact = write_payload_sent(tmp_path / "runs", task_id=row.id, attempt=2, text=second.text)
    data = (tmp_path / "runs" / artifact.relative_path).read_bytes()
    assert data == second.text.encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == artifact.sha256 == second.sha256
    assert _email().encode() not in data and _email("attachment").encode() not in data
    assert not hasattr(first, "mapping")
    assert _email() not in repr(first)


def test_maps_never_appear_in_owner_task_serialization(
    db_session: Session, settings: PrivacySettings
) -> None:
    row, task = _store_task(db_session)
    prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=1, settings=settings)
    db_session.flush()

    detail = task_detail(row).model_dump(mode="json")
    serialized = json.dumps(detail)
    assert "redaction_maps" not in serialized and "file_hashes" not in serialized
    assert "original_sha256" not in serialized and "first_attempt" not in serialized
    assert "[[CD_" not in serialized
    assert _map_count(db_session) == 1


def test_other_task_cannot_rehydrate_prepared_payload(
    db_session: Session, settings: PrivacySettings
) -> None:
    first_row, first_task = _store_task(db_session)
    second_row, second_task = _store_task(db_session, instructions=_email("second"))
    first = prepare_task_payload(
        db_session, first_task, lane=Worker.CLAUDE, attempt=1, settings=settings
    )
    prepare_task_payload(db_session, second_task, lane=Worker.CLAUDE, attempt=1, settings=settings)

    with pytest.raises(RedactionError) as caught:
        rehydrate_for_task(db_session, task_id=second_row.id, text=first.text)
    assert _email() not in str(caught.value)
    assert rehydrate_for_task(db_session, task_id=first_row.id, text=first.text) == render_payload(
        first_task, settings=settings
    )


def test_uncommitted_payload_map_is_rolled_back(
    db_session: Session, settings: PrivacySettings
) -> None:
    _, task = _store_task(db_session)
    db_session.commit()
    prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=1, settings=settings)
    assert _map_count(db_session) == 1
    db_session.rollback()
    assert _map_count(db_session) == 0


def test_secret_payload_never_allocates_a_map(
    db_session: Session, settings: PrivacySettings
) -> None:
    secret = "DECK_OWNER_TOKEN=" + "synthetic-value"
    _, task = _store_task(db_session, instructions=_email() + " " + secret)
    db_session.commit()
    with pytest.raises(PrivacyInputError) as caught:
        prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=1, settings=settings)
    assert secret not in str(caught.value)
    assert _map_count(db_session) == 0


def test_changed_task_body_cannot_allocate_against_an_existing_identity(
    db_session: Session, settings: PrivacySettings
) -> None:
    _, task = _store_task(db_session)
    payload = task.model_dump(mode="json")
    payload["instructions"] = _email("different")
    different = TaskV1.model_validate(payload)

    with pytest.raises(ValueError):
        prepare_task_payload(
            db_session, different, lane=Worker.CLAUDE, attempt=1, settings=settings
        )
    assert _map_count(db_session) == 0


def test_missing_task_does_not_allocate(db_session: Session, settings: PrivacySettings) -> None:
    payload = _minimal_task_payload(uuid4())
    payload.update(data_class="cloud_redacted", instructions=_email())
    with pytest.raises(ValueError):
        prepare_task_payload(
            db_session,
            TaskV1.model_validate(payload),
            lane=Worker.CLAUDE,
            attempt=1,
            settings=settings,
        )
    assert _map_count(db_session) == 0


def test_final_payload_rejection_rolls_back_only_new_mapping_rows(
    db_session: Session, settings: PrivacySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.inbox / "brief.txt"
    path.write_text("ordinary attachment", encoding="utf-8")
    _, task = _store_task(
        db_session, inputs=[{"type": "file", "name": "brief", "path": "brief.txt"}]
    )
    prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=1, settings=settings)
    db_session.commit()
    originals = {row.id for row in db_session.scalars(select(RedactionMap))}
    assert len(originals) == 1
    path.write_text(_email("additional"), encoding="utf-8")
    actual_allocate = payload_module.redact_for_task

    def allocate_then_expand(*args, **kwargs):
        outcome = actual_allocate(*args, **kwargs)
        assert _map_count(db_session) == 2
        return replace(outcome, text="ü" * (5 * 1024 * 1024 // 2 + 1))

    monkeypatch.setattr(payload_module, "redact_for_task", allocate_then_expand)
    with pytest.raises(PrivacyInputError):
        prepare_task_payload(db_session, task, lane=Worker.CLAUDE, attempt=2, settings=settings)
    db_session.commit()
    assert {row.id for row in db_session.scalars(select(RedactionMap))} == originals
