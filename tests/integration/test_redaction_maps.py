"""Local redaction allocation is stable, atomic, and isolated by task."""

from __future__ import annotations

import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import DataClass
from app.db.models import RedactionMap, Task
from app.db.session import get_engine
from app.db.state_machine import TaskState
from app.redaction import repository
from app.redaction.engine import RedactionError
from app.redaction.placeholders import RedactionKind, format_placeholder
from app.redaction.repository import RedactionStorageError, redact_for_task, rehydrate_for_task
from tests.integration.test_database import _minimal_task_payload


def _task(session: Session, data_class: DataClass = DataClass.CLOUD_REDACTED) -> Task:
    task_id = uuid.uuid4()
    payload = _minimal_task_payload(task_id)
    payload["data_class"] = data_class.value
    task = Task(
        id=task_id, status=TaskState.AWAITING_APPROVAL, data_class=data_class, payload=payload
    )
    session.add(task)
    session.flush()
    return task


def _email(name: str = "synthetic") -> str:
    return name + "@" + "example.invalid"


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(RedactionMap)) or 0


def test_repeated_values_and_retries_keep_original_identifiers(db_session: Session) -> None:
    task = _task(db_session)
    first = redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    db_session.commit()
    rows_before = list(db_session.scalars(select(RedactionMap)))
    stored_ids = {row.placeholder: row.id for row in rows_before}

    repeated_text = _email() + " / " + _email() + " / " + _email("second")
    second = redact_for_task(db_session, task_id=task.id, text=repeated_text, attempt=2)
    db_session.commit()
    rows = list(db_session.scalars(select(RedactionMap)))

    assert first.text in second.text
    assert second.text.count(first.text) == 2
    assert len(rows) == 2
    assert rehydrate_for_task(db_session, task_id=task.id, text=second.text) == repeated_text
    assert {row.placeholder: row.id for row in rows if row.first_attempt == 1} == stored_ids
    assert sorted(row.first_attempt for row in rows) == [1, 2]
    assert all(row.original not in repr(row) for row in rows)


def test_allocation_is_rolled_back_with_callers_transaction(db_session: Session) -> None:
    task = _task(db_session)
    db_session.commit()
    redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    assert _count(db_session) == 1
    db_session.rollback()
    assert _count(db_session) == 0


def test_new_classification_keeps_existing_original_identifier(db_session: Session) -> None:
    task = _task(db_session)
    original = _email()
    first = redact_for_task(db_session, task_id=task.id, text=original, attempt=1)
    db_session.commit()
    second = redact_for_task(
        db_session, task_id=task.id, text=original, attempt=2, blocklist=(original,)
    )
    assert second.text == first.text
    assert second.mapping == first.mapping
    assert _count(db_session) == 1
    row = db_session.scalar(select(RedactionMap))
    assert row is not None and row.kind == "EMAIL" and row.first_attempt == 1


def test_rejected_secret_leaves_existing_map_unchanged(db_session: Session) -> None:
    task = _task(db_session)
    first = redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    db_session.commit()
    secret = "sk-" + "TEST-SYNTHETIC-CREDENTIAL"

    with pytest.raises(RedactionError) as rejected:
        redact_for_task(
            db_session, task_id=task.id, text=_email("second") + " " + secret, attempt=2
        )

    assert secret not in str(rejected.value)
    assert _count(db_session) == 1
    assert rehydrate_for_task(db_session, task_id=task.id, text=first.text) == _email()


def test_foreign_or_unknown_placeholders_never_resolve(db_session: Session) -> None:
    first_task = _task(db_session)
    second_task = _task(db_session)
    first = redact_for_task(db_session, task_id=first_task.id, text=_email(), attempt=1)
    redact_for_task(db_session, task_id=second_task.id, text=_email("second"), attempt=1)
    foreign = first.text
    unknown = format_placeholder(first_task.id, RedactionKind.EMAIL, 999)

    with pytest.raises(RedactionError):
        rehydrate_for_task(db_session, task_id=second_task.id, text=foreign)
    with pytest.raises(RedactionError):
        rehydrate_for_task(db_session, task_id=first_task.id, text=unknown)
    assert _count(db_session) == 2


@pytest.mark.parametrize("data_class", [DataClass.LOCAL_ONLY, DataClass.CLOUD_OK])
def test_other_classes_cannot_create_or_read_maps(
    db_session: Session, data_class: DataClass
) -> None:
    task = _task(db_session, data_class)
    with pytest.raises(RedactionStorageError):
        redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    with pytest.raises(RedactionStorageError):
        rehydrate_for_task(db_session, task_id=task.id, text="ordinary output")
    assert _count(db_session) == 0


@pytest.mark.parametrize("attempt", [0, -1, True, "1"])
def test_invalid_attempt_never_allocates(db_session: Session, attempt) -> None:
    task = _task(db_session)
    with pytest.raises(RedactionStorageError):
        redact_for_task(db_session, task_id=task.id, text=_email(), attempt=attempt)
    assert _count(db_session) == 0


def test_missing_task_does_not_create_or_resolve_maps(db_session: Session) -> None:
    task_id = uuid.uuid4()
    with pytest.raises(RedactionStorageError):
        redact_for_task(db_session, task_id=task_id, text=_email(), attempt=1)
    with pytest.raises(RedactionStorageError):
        rehydrate_for_task(db_session, task_id=task_id, text="ordinary output")
    assert _count(db_session) == 0


def test_concurrent_allocations_share_identifiers_without_duplicates(db_session: Session) -> None:
    task = _task(db_session)
    task_id = task.id
    db_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    ready = threading.Barrier(2)

    def allocate(label: str):
        with factory.begin() as session:
            ready.wait(timeout=5)
            return redact_for_task(
                session, task_id=task_id, text=_email() + " " + _email(label), attempt=1
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(allocate, label) for label in ("first", "second")]
        outcomes = [future.result(timeout=10) for future in futures]

    rows = list(db_session.scalars(select(RedactionMap)))
    assert len(rows) == 3
    shared = next(row.placeholder for row in rows if row.original == _email())
    assert all(shared in outcome.text for outcome in outcomes)
    assert len({row.placeholder for row in rows}) == 3


def test_large_original_uses_bounded_hash_index(db_session: Session) -> None:
    task = _task(db_session)
    # Direct storage verifies PostgreSQL's index contract without extending scan limits.
    original = "synthetic " * 1000
    db_session.add(
        RedactionMap(
            task_id=task.id,
            placeholder=format_placeholder(task.id, RedactionKind.TERM, 1),
            kind="TERM",
            original=original,
            original_sha256=hashlib.sha256(original.encode()).hexdigest(),
            first_attempt=1,
        )
    )
    db_session.flush()
    assert _count(db_session) == 1


def test_existing_entries_are_immutable_at_database_boundary(db_session: Session) -> None:
    task = _task(db_session)
    redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    db_session.commit()
    with (
        pytest.raises(DBAPIError, match="redaction map entries are immutable"),
        db_session.begin_nested(),
    ):
        db_session.execute(text("UPDATE redaction_maps SET first_attempt = 2"))
    assert db_session.scalar(select(RedactionMap.first_attempt)) == 1


@pytest.mark.parametrize(
    "violation", ["foreign_scope", "digest", "duplicate_original", "duplicate_other_kind"]
)
def test_database_rejects_invalid_or_duplicate_map_rows(
    db_session: Session, violation: str
) -> None:
    task = _task(db_session)
    original = _email()
    first = redact_for_task(db_session, task_id=task.id, text=original, attempt=1)
    db_session.commit()
    placeholder = format_placeholder(task.id, RedactionKind.EMAIL, 2)
    kind = "EMAIL"
    digest = hashlib.sha256(original.encode()).hexdigest()
    if violation == "foreign_scope":
        placeholder = format_placeholder(uuid.uuid4(), RedactionKind.EMAIL, 2)
    elif violation == "digest":
        digest = "0" * 64
    elif violation == "duplicate_other_kind":
        placeholder = format_placeholder(task.id, RedactionKind.TERM, 1)
        kind = "TERM"
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            RedactionMap(
                task_id=task.id,
                placeholder=placeholder,
                kind=kind,
                original=original,
                original_sha256=digest,
                first_attempt=2,
            )
        )
        db_session.flush()
    assert _count(db_session) == 1
    assert rehydrate_for_task(db_session, task_id=task.id, text=first.text) == original


def test_task_deletion_is_restricted_until_local_map_is_removed(db_session: Session) -> None:
    task = _task(db_session)
    task_id = task.id
    redact_for_task(db_session, task_id=task_id, text=_email(), attempt=1)
    db_session.commit()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
    db_session.execute(text("DELETE FROM redaction_maps WHERE task_id = :id"), {"id": task_id})
    db_session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
    assert _count(db_session) == 0


def test_digest_collision_is_rejected_without_overwriting_original(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(db_session)
    first = redact_for_task(db_session, task_id=task.id, text=_email(), attempt=1)
    db_session.commit()
    existing_digest = hashlib.sha256(_email().encode()).hexdigest()

    class CollidingDigest:
        def hexdigest(self) -> str:
            return existing_digest

    monkeypatch.setattr(repository, "sha256", lambda value: CollidingDigest())
    with pytest.raises(RedactionStorageError, match="digest collision") as rejected:
        redact_for_task(db_session, task_id=task.id, text=_email("second"), attempt=2)
    assert _email("second") not in str(rejected.value)
    assert _count(db_session) == 1
    assert rehydrate_for_task(db_session, task_id=task.id, text=first.text) == _email()
