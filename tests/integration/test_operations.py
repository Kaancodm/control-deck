"""Real isolated dump/restore and fenced retention, without production access."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1
from app.db.config import get_test_database_url
from app.db.models import Event, Lease, RedactionMap, Result, Task
from app.db.repository import create_task_from_v1
from app.db.state_machine import TaskState
from app.kb.state import ContextSnapshot, KnowledgeExport
from app.operations import backup, retention
from app.operations.audit import record_operation
from app.operations.errors import OperationError
from app.operations.files import child_directory, private_root, write_file
from app.operations.models import OperationEvent
from app.operations.restic import copy_with_restic
from app.operations.restore import restore_backup
from tests.integration.test_database import _minimal_task_payload


def _task(session: Session, *, status=TaskState.DONE, age=timedelta(days=91)) -> Task:
    task = create_task_from_v1(session, TaskV1.model_validate(_minimal_task_payload(uuid4())))
    task.status = status
    task.updated_at = datetime.now(UTC) - age
    session.commit()
    return task


def _artifacts(root: Path, task_id) -> None:
    with (
        private_root(root, create=True) as parent,
        child_directory(parent, str(task_id), create=True) as task_directory,
    ):
        with child_directory(task_directory, "1", create=True) as attempt:
            write_file(attempt, "payload.sent.md", b"technical fixture")


def _local_records(session: Session, task_id) -> None:
    content = "technical fixture"
    digest = hashlib.sha256(content.encode()).hexdigest()
    session.add(
        RedactionMap(
            task_id=task_id,
            placeholder=f"[[CD_{task_id.hex}_TERM_1]]",
            kind="TERM",
            original=content,
            original_sha256=digest,
            first_attempt=1,
        )
    )
    session.add(
        ContextSnapshot(
            task_id=task_id,
            position=0,
            reference=uuid4(),
            data_class="local_only",
            content=content,
            content_sha256=digest,
        )
    )
    session.commit()


def test_retention_removes_only_artifacts_maps_and_snapshots(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task(db_session)
    task_id = task.id
    _local_records(db_session, task_id)
    stored_result = Result(
        task_id=task_id,
        attempt=1,
        payload={
            "schema_version": "result.v1",
            "task_id": str(task_id),
            "attempt": 1,
        },
    )
    db_session.add(stored_result)
    db_session.flush()
    db_session.add(
        KnowledgeExport(
            task_id=task_id,
            document_id=uuid4(),
            result_id=stored_result.id,
            data_class="local_only",
            content_sha256="0" * 64,
            state="completed",
        )
    )
    db_session.commit()
    root = tmp_path / "runs"
    _artifacts(root, task_id)
    event_count = db_session.scalar(select(func.count()).select_from(Event))
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    assert retention.retain_tasks(factory, root).candidates == 1
    assert (root / str(task_id)).exists()
    assert db_session.scalar(select(func.count()).select_from(OperationEvent)) == 0
    result = retention.retain_tasks(factory, root, apply=True)
    assert result.completed == 1 and result.failed == 0
    db_session.expire_all()
    assert db_session.get(Task, task_id) is not None
    assert db_session.scalar(select(func.count()).select_from(Event)) == event_count
    assert db_session.scalar(select(func.count()).select_from(Result)) == 1
    assert db_session.scalar(select(func.count()).select_from(KnowledgeExport)) == 1
    assert db_session.scalar(select(func.count()).select_from(RedactionMap)) == 0
    assert db_session.scalar(select(func.count()).select_from(ContextSnapshot)) == 0
    assert not (root / str(task_id)).exists()
    assert retention.retain_tasks(factory, root, apply=True).candidates == 0
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.task_id == task_id)
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "artifacts_removed", "completed"]


@pytest.mark.parametrize(
    "status",
    [
        TaskState.AWAITING_APPROVAL,
        TaskState.QUEUED,
        TaskState.LEASED,
        TaskState.RUNNING,
        TaskState.REVIEW,
    ],
)
def test_retention_preserves_all_old_nonterminal_tasks(
    db_session: Session, tmp_path: Path, status: TaskState
) -> None:
    task = _task(db_session, status=status, age=timedelta(days=365))
    _local_records(db_session, task.id)
    root = tmp_path / "runs"
    _artifacts(root, task.id)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    result = retention.retain_tasks(factory, root, apply=True)
    assert result.candidates == 0
    assert (root / str(task.id)).exists()
    assert db_session.scalar(select(func.count()).select_from(RedactionMap)) == 1


def test_cancelled_task_with_unacknowledged_lease_is_preserved(
    db_session: Session, tmp_path: Path
) -> None:
    task = _task(db_session, status=TaskState.CANCELLED)
    now = datetime.now(UTC)
    db_session.add(
        Lease(
            lease_id=uuid4(),
            task_id=task.id,
            expires_at=now - timedelta(days=1),
            heartbeat_at=now - timedelta(days=2),
        )
    )
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    assert retention.retain_tasks(factory, tmp_path / "runs", apply=True).candidates == 0


def test_retention_90_day_boundary_uses_task_timestamp(db_session: Session, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    task = _task(db_session)
    task.updated_at = now - timedelta(days=90)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    assert retention.retain_tasks(factory, tmp_path / "runs", now=now).candidates == 0
    assert (
        retention.retain_tasks(
            factory, tmp_path / "runs", now=now + timedelta(microseconds=1)
        ).candidates
        == 1
    )


def test_failed_filesystem_cleanup_preserves_maps_then_resumes(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(db_session)
    _local_records(db_session, task.id)
    root = tmp_path / "runs"
    _artifacts(root, task.id)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    original = retention.remove_tree

    def failure(*args, **kwargs):
        raise OperationError("file_unavailable")

    monkeypatch.setattr(retention, "remove_tree", failure)
    assert retention.retain_tasks(factory, root, apply=True).failed == 1
    assert db_session.scalar(select(func.count()).select_from(RedactionMap)) == 1
    assert (root / (".retention-" + task.id.hex)).exists()
    monkeypatch.setattr(retention, "remove_tree", original)
    assert retention.retain_tasks(factory, root, apply=True).completed == 1
    assert db_session.scalar(select(func.count()).select_from(RedactionMap)) == 0


def test_operation_events_are_append_only(db_session: Session) -> None:
    event = record_operation(
        db_session, operation_id=uuid4(), operation_type="backup", phase="started"
    )
    db_session.commit()
    event_id = event.id
    for statement in (
        "UPDATE operations_events SET phase='completed' WHERE id=:id",
        "DELETE FROM operations_events WHERE id=:id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            db_session.execute(text(statement), {"id": event_id})
            db_session.commit()
        db_session.rollback()


@pytest.fixture
def restore_target(db_session: Session):
    source_url = make_url(get_test_database_url())
    name = "deck_restore_" + uuid4().hex + "_test"
    with (
        db_session.get_bind()
        .connect()
        .execution_options(isolation_level="AUTOCOMMIT") as connection
    ):
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = source_url.set(database=name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
        with (
            db_session.get_bind()
            .connect()
            .execution_options(isolation_level="AUTOCOMMIT") as connection
        ):
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _client(name: str) -> tuple[str, ...]:
    selected = os.environ.get("DECK_TEST_" + name.upper()) or shutil.which(name)
    assert selected, (
        f"A matching PostgreSQL client is required for {name}; restore proof must not skip"
    )
    return (selected,)


def test_real_backup_and_restore_share_snapshot_counts(
    db_session: Session, tmp_path: Path, restore_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _task(db_session, status=TaskState.AWAITING_APPROVAL)
    first_id = first.id
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    original = backup.run_command
    inserted = []

    def concurrent_change(command, **kwargs):
        with factory() as session:
            inserted.append(_task(session).id)
        return original(command, **kwargs)

    monkeypatch.setattr(backup, "run_command", concurrent_change)
    result = backup.create_backup(
        get_test_database_url(), tmp_path / "backups", dump_command=_client("pg_dump")
    )
    restore_backup(
        tmp_path / "backups",
        result.name,
        target_url=restore_target,
        audit_url=get_test_database_url(),
        restore_command=_client("pg_restore"),
    )
    target_engine = create_engine(restore_target)
    try:
        with Session(target_engine) as session:
            assert session.get(Task, first_id) is not None
            assert session.get(Task, inserted[0]) is None
            assert session.scalar(select(func.count()).select_from(Task)) == 1
    finally:
        target_engine.dispose()
    assert db_session.scalar(select(func.count()).select_from(Task)) == 2
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "restore")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "completed"]


def test_failed_dump_is_not_published(db_session: Session, tmp_path: Path) -> None:
    root = tmp_path / "backups"
    with pytest.raises(OperationError):
        backup.create_backup(
            get_test_database_url(),
            root,
            dump_command=(
                sys.executable,
                "-c",
                "import sys;sys.stdout.write('partial');sys.exit(1)",
            ),
        )
    assert not list(root.glob("backup-*"))
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "backup")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "failed"]


def test_restore_rejects_nonempty_target_before_process(
    db_session: Session, tmp_path: Path, restore_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = create_engine(restore_target)
    with target.begin() as connection:
        connection.execute(text("CREATE TABLE keep_data (id integer)"))
        connection.execute(text("INSERT INTO keep_data VALUES (42)"))
    from app.operations import restore

    monkeypatch.setattr(restore, "run_command", lambda *args, **kwargs: pytest.fail("No process"))
    with pytest.raises(OperationError) as error:
        restore_backup(
            tmp_path / "missing",
            "backup-" + uuid4().hex,
            target_url=restore_target,
            audit_url=get_test_database_url(),
        )
    assert error.value.code == "restore_target_not_empty"
    with target.connect() as connection:
        assert connection.scalar(text("SELECT id FROM keep_data")) == 42
    target.dispose()


def test_optional_restic_hook_uses_fake_cli_and_private_credential_descriptor(
    db_session: Session, tmp_path: Path
) -> None:
    backup_root, runs_root, configuration = (
        tmp_path / name for name in ("backups", "runs", "configuration")
    )
    for root in (backup_root, runs_root, configuration):
        root.mkdir(mode=0o700)
    password = configuration / "password"
    password.write_text("fake-restic-password")
    password.chmod(0o600)
    fake = (
        "import os,sys;"
        "assert open(os.environ['RESTIC_PASSWORD_FILE']).read()=='fake-restic-password';"
        "assert 'DECK_DATABASE_URL' not in os.environ;"
        "assert sys.argv[1:3]==['backup','--']"
    )
    copy_with_restic(
        get_test_database_url(),
        backup_root=backup_root,
        runs_root=runs_root,
        configuration={
            "RESTIC_REPOSITORY": "fake-repository",
            "RESTIC_PASSWORD_FILE": str(password),
        },
        command=(sys.executable, "-c", fake),
    )
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.detail["reason"].astext == "restic_copy")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "completed"]


def test_restic_hook_refuses_unsafe_credential_before_process(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.operations import restic

    roots = [tmp_path / name for name in ("backups", "runs", "configuration")]
    for root in roots:
        root.mkdir(mode=0o700)
    password = roots[2] / "password"
    password.write_text("fake-only")
    password.chmod(0o644)
    monkeypatch.setattr(restic, "run_command", lambda *args, **kwargs: pytest.fail("No child"))
    with pytest.raises(OperationError):
        copy_with_restic(
            get_test_database_url(),
            backup_root=roots[0],
            runs_root=roots[1],
            configuration={
                "RESTIC_REPOSITORY": "fake-repository",
                "RESTIC_PASSWORD_FILE": str(password),
            },
        )
    assert (
        db_session.scalar(select(OperationEvent.phase).order_by(OperationEvent.id.desc()).limit(1))
        == "failed"
    )
