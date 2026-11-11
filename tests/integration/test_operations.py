"""Real isolated dump/restore and fenced retention, without production access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import DataClass, TaskV1
from app.db.config import get_test_database_url
from app.db.models import Event, Lease, RedactionMap, Result, Task
from app.db.repository import create_task_from_v1
from app.db.state_machine import TaskState
from app.kb.config import get_kb_test_database_url
from app.kb.contracts import EmbeddingSpec
from app.kb.repository import KnowledgeStore
from app.kb.state import ContextSnapshot, KnowledgeExport
from app.operations import backup, retention
from app.operations.audit import record_operation
from app.operations.errors import OperationError
from app.operations.files import child_directory, private_root, write_file
from app.operations.models import OperationEvent
from app.operations.restic import copy_with_restic
from app.operations.restore import restore_backup
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.integration.test_knowledge_store import knowledge_store as knowledge_store
from tests.support.kb import contract_row, use_contract


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


def test_operations_migration_preserves_evidence_and_roundtrips(db_session: Session) -> None:
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    record_operation(db_session, operation_id=uuid4(), operation_type="backup", phase="started")
    db_session.commit()
    current = db_session.scalar(text("SELECT version_num FROM alembic_version"))
    db_session.commit()
    try:
        with pytest.raises(RuntimeError, match="maintenance evidence would be lost"):
            command.downgrade(config, "20251103_0014")
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == current
        assert db_session.scalar(select(func.count()).select_from(OperationEvent)) == 1
        db_session.execute(text("TRUNCATE operations_events"))
        db_session.commit()
        command.downgrade(config, "20251103_0014")
        assert db_session.scalar(text("SELECT to_regclass('operations_events')")) is None
        db_session.commit()
    finally:
        db_session.rollback()
        command.upgrade(config, "head")
    command.check(config)


@pytest.fixture(autouse=True)
def operations_knowledge_database(kb_engine):
    """Operations always protect the separate migrated knowledge database."""
    yield


@pytest.fixture
def knowledge_restore_target(db_session):
    yield from restore_target.__wrapped__(db_session)


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
    db_session: Session,
    tmp_path: Path,
    restore_target: str,
    knowledge_restore_target: str,
    monkeypatch: pytest.MonkeyPatch,
    knowledge_store,
) -> None:
    first = _task(db_session, status=TaskState.AWAITING_APPROVAL)
    first_id = first.id
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    original = backup.run_command
    inserted = []
    document_id, later_document = uuid4(), uuid4()
    content = "Synthetic knowledge retained with its vector and immutable contract"
    knowledge_store.put_document(
        document_id=document_id,
        source_task_id=first_id,
        title="Backup evidence",
        content=content,
        data_class=DataClass.LOCAL_ONLY,
    )

    def concurrent_change(command, **kwargs):
        if "--schema=deck_kb" not in command:
            with factory() as session:
                inserted.append(_task(session).id)
        else:
            knowledge_store.put_document(
                document_id=later_document,
                title="After snapshot",
                content="Later knowledge",
                data_class=DataClass.LOCAL_ONLY,
            )
        return original(command, **kwargs)

    monkeypatch.setattr(backup, "run_command", concurrent_change)
    result = backup.create_backup(
        get_test_database_url(),
        tmp_path / "backups",
        knowledge_url=get_kb_test_database_url(),
        dump_command=_client("pg_dump"),
    )
    restore_backup(
        tmp_path / "backups",
        result.name,
        target_url=restore_target,
        knowledge_target_url=knowledge_restore_target,
        knowledge_source_url=get_kb_test_database_url(),
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
    knowledge_target = create_engine(knowledge_restore_target)
    try:
        store = KnowledgeStore(knowledge_target)
        blocks = store.resolve((str(document_id),), task_class=DataClass.LOCAL_ONLY)
        assert blocks[0].text.endswith("\n" + content)
        hits = store.search(content, task_class=DataClass.LOCAL_ONLY, limit=5)
        assert [hit.reference for hit in hits] == [str(document_id)]
        with knowledge_target.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM deck_kb.documents")) == 1
            assert connection.scalar(text("SELECT content FROM deck_kb.chunks")) == content
            assert (
                connection.scalar(text("SELECT source_task_id FROM deck_kb.documents")) == first_id
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
                == 1
            )
    finally:
        knowledge_target.dispose()
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
            knowledge_url=get_kb_test_database_url(),
            dump_command=(
                sys.executable,
                "-c",
                "import sys;sys.stdout.write('partial');sys.exit(1)",
            ),
        )
    assert not list(root.glob("backup-*"))
    assert not list(root.glob(".pending-*"))
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "backup")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "failed"]


@pytest.mark.parametrize("change", ["corrupt_dump", "target_changed"])
def test_restore_rechecks_target_and_archive_before_schema_preparation(
    db_session: Session,
    tmp_path: Path,
    restore_target: str,
    knowledge_restore_target: str,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from app.operations import restore

    root = tmp_path / "backups"
    result = backup.create_backup(
        get_test_database_url(),
        root,
        knowledge_url=get_kb_test_database_url(),
        dump_command=_client("pg_dump"),
    )
    target = create_engine(restore_target)
    if change == "corrupt_dump":
        with (root / result.name / "database.dump").open("ab") as output:
            output.write(b"corrupt")
    else:
        original = restore.digest_file

        changed = []

        def changed_target(*args, **kwargs):
            evidence = original(*args, **kwargs)
            if not changed:
                with target.begin() as connection:
                    connection.execute(text("CREATE TABLE preserve_racing_data (id integer)"))
                    connection.execute(text("INSERT INTO preserve_racing_data VALUES (42)"))
                changed.append(True)
            return evidence

        monkeypatch.setattr(restore, "digest_file", changed_target)
    monkeypatch.setattr(restore, "run_command", lambda *args, **kwargs: pytest.fail("No restore"))
    try:
        with pytest.raises(OperationError) as error:
            restore_backup(
                root,
                result.name,
                target_url=restore_target,
                audit_url=get_test_database_url(),
                knowledge_target_url=knowledge_restore_target,
                knowledge_source_url=get_kb_test_database_url(),
            )
        assert error.value.code == (
            "backup_checksum_mismatch" if change == "corrupt_dump" else "restore_target_not_empty"
        )
        with target.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname='public'"))
                == 1
            )
            if change == "target_changed":
                assert connection.scalar(text("SELECT id FROM preserve_racing_data")) == 42
    finally:
        target.dispose()


def test_restore_rejects_nonempty_target_before_process(
    db_session: Session,
    tmp_path: Path,
    restore_target: str,
    knowledge_restore_target: str,
    monkeypatch: pytest.MonkeyPatch,
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
            knowledge_target_url=knowledge_restore_target,
            knowledge_source_url=get_kb_test_database_url(),
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
    _complete_backup(tmp_path)
    password = configuration / "password"
    password.write_text("fake-restic-password")
    password.chmod(0o600)
    fake = (
        "import os,sys;"
        "assert open(os.environ['RESTIC_PASSWORD_FILE']).read()=='fake-restic-password';"
        "assert 'DECK_DATABASE_URL' not in os.environ;"
        "assert sys.argv[1:3]==['backup','--'];"
        "from pathlib import Path;"
        "assert len(list(Path(sys.argv[3]).glob('backup-*/knowledge.dump')))==1"
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


def _complete_backup(tmp_path: Path):
    return backup.create_backup(
        get_test_database_url(),
        tmp_path / "backups",
        knowledge_url=get_kb_test_database_url(),
        dump_command=_client("pg_dump"),
    )


def _restore_complete(tmp_path, name, central, knowledge, **kwargs):
    return restore_backup(
        tmp_path / "backups",
        name,
        target_url=central,
        audit_url=get_test_database_url(),
        knowledge_target_url=knowledge,
        knowledge_source_url=get_kb_test_database_url(),
        restore_command=_client("pg_restore"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "corruption,expected",
    [
        ("dump", "backup_checksum_mismatch"),
        ("checksum", "backup_checksum_mismatch"),
        ("missing", "unsafe_path"),
        ("revision", "restore_revision_mismatch"),
        ("schema", "invalid_backup_manifest"),
    ],
)
def test_invalid_knowledge_archive_never_mutates_either_target(
    db_session,
    tmp_path,
    restore_target,
    knowledge_restore_target,
    monkeypatch,
    corruption,
    expected,
):
    from app.operations import restore

    result = _complete_backup(tmp_path)
    directory = tmp_path / "backups" / result.name
    if corruption == "dump":
        with (directory / "knowledge.dump").open("ab") as output:
            output.write(b"changed")
    elif corruption == "checksum":
        (directory / "knowledge.sha256").write_text("0" * 64 + "\n")
    elif corruption == "missing":
        (directory / "knowledge.dump").unlink()
    else:
        manifest = json.loads((directory / "manifest.json").read_text())
        if corruption == "revision":
            manifest["knowledge"]["schema_revision"] = "kb_9999"
        else:
            manifest["knowledge"]["row_counts"]["unexpected.documents"] = 1
        (directory / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(restore, "run_command", lambda *a, **k: pytest.fail("No process"))
    with pytest.raises(OperationError) as error:
        _restore_complete(tmp_path, result.name, restore_target, knowledge_restore_target)
    assert error.value.code == expected
    for url in (restore_target, knowledge_restore_target):
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                restore._require_empty_target(connection)
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM pg_namespace WHERE nspname='public'")
                    )
                    == 1
                )
        finally:
            engine.dispose()
    assert (
        "completed"
        not in db_session.scalars(
            select(OperationEvent.phase).where(OperationEvent.operation_type == "restore")
        ).all()
    )


def test_nonempty_knowledge_target_preserves_both_databases(
    db_session,
    tmp_path,
    restore_target,
    knowledge_restore_target,
    monkeypatch,
):
    from app.operations import restore

    target = create_engine(knowledge_restore_target)
    try:
        with target.begin() as connection:
            connection.execute(text("CREATE TABLE preserved (id integer)"))
            connection.execute(text("INSERT INTO preserved VALUES (42)"))
        monkeypatch.setattr(restore, "run_command", lambda *a, **k: pytest.fail("No process"))
        with pytest.raises(OperationError) as error:
            _restore_complete(
                tmp_path, "backup-" + uuid4().hex, restore_target, knowledge_restore_target
            )
        assert error.value.code == "restore_target_not_empty"
        with target.connect() as connection:
            assert connection.scalar(text("SELECT id FROM preserved")) == 42
        central = create_engine(restore_target)
        try:
            with central.connect() as connection:
                restore._require_empty_target(connection)
        finally:
            central.dispose()
    finally:
        target.dispose()


def test_second_dump_failure_never_publishes_a_central_only_set(db_session, tmp_path, monkeypatch):
    original = backup.run_command
    dumped = []

    def second_fails(command, **kwargs):
        if "--schema=deck_kb" in command:
            raise OperationError("process_failed")
        original(command, **kwargs)
        dumped.append(True)

    monkeypatch.setattr(backup, "run_command", second_fails)
    with pytest.raises(OperationError):
        _complete_backup(tmp_path)
    assert dumped == [True]
    assert not list((tmp_path / "backups").iterdir())
    assert db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "backup")
        .order_by(OperationEvent.id)
    ).all() == ["started", "failed"]


@pytest.mark.parametrize(
    "failure", ["second_process", "row_evidence", "knowledge_check", "knowledge_contract"]
)
def test_partial_restore_never_reports_success(
    db_session,
    tmp_path,
    restore_target,
    knowledge_restore_target,
    monkeypatch,
    failure,
    knowledge_store,
):
    from app.operations import restore

    task_id = _task(db_session).id
    if failure == "knowledge_contract":
        with knowledge_store.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE deck_kb.documents ALTER COLUMN data_class SET DEFAULT 'local_only'"
                )
            )
    try:
        result = _complete_backup(tmp_path)
    finally:
        if failure == "knowledge_contract":
            with knowledge_store.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE deck_kb.documents ALTER COLUMN data_class DROP DEFAULT")
                )
    if failure == "row_evidence":
        path = tmp_path / "backups" / result.name / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["knowledge"]["row_counts"]["deck_kb.documents"] += 1
        path.write_text(json.dumps(manifest))
    original = restore.run_command

    def fail_second(command, **kwargs):
        if (
            "--dbname" in command
            and kwargs["env"]["PGDATABASE"] == make_url(knowledge_restore_target).database
        ):
            raise OperationError("process_failed")
        return original(command, **kwargs)

    if failure == "second_process":
        monkeypatch.setattr(restore, "run_command", fail_second)
    kwargs = (
        {"knowledge_check_command": (sys.executable, "-c", "raise SystemExit(1)")}
        if failure == "knowledge_check"
        else {}
    )
    with pytest.raises(OperationError) as error:
        _restore_complete(tmp_path, result.name, restore_target, knowledge_restore_target, **kwargs)
    assert error.value.code == "restore_partial_failure"
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "restore")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "failed"]
    assert (
        db_session.scalar(
            select(OperationEvent.detail).where(
                OperationEvent.operation_type == "restore", OperationEvent.phase == "failed"
            )
        )["reason"]
        == "restore_partial_failure"
    )
    central = create_engine(restore_target)
    try:
        with Session(central) as session:
            assert session.get(Task, task_id) is not None
    finally:
        central.dispose()


@pytest.mark.parametrize("source", ["argument", "environment", "archive_version"])
def test_restore_checks_contract_against_configured_spec(
    db_session,
    tmp_path,
    restore_target,
    knowledge_restore_target,
    monkeypatch,
    source,
    knowledge_store,
):
    from app.operations import restore

    _task(db_session)
    if source == "archive_version":
        with knowledge_store.engine.begin() as connection:
            connection.execute(text("UPDATE deck_kb.contract SET version='unknown'"))
    result = _complete_backup(tmp_path)
    real = EmbeddingSpec("ollama", "qwen3-embedding:0.6b", 1024)
    kwargs = {}
    if source == "argument":
        kwargs["embedding_spec"] = real
    elif source == "environment":
        monkeypatch.setenv("DECK_KB_EMBEDDING_PROVIDER", real.provider)
        monkeypatch.setenv("DECK_KB_EMBEDDING_MODEL", real.model)
        monkeypatch.setenv("DECK_KB_EMBEDDING_DIMENSION", str(real.dimension))
    with pytest.raises(OperationError) as error:
        _restore_complete(tmp_path, result.name, restore_target, knowledge_restore_target, **kwargs)
    assert error.value.code == "knowledge_contract_mismatch"
    for url in (restore_target, knowledge_restore_target):
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                restore._require_empty_target(connection)
                assert connection.scalar(text("SELECT to_regclass('public.tasks')")) is None
                assert connection.scalar(text("SELECT to_regnamespace('deck_kb')")) is None
        finally:
            engine.dispose()
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.operation_type == "restore")
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["failed"]


def test_restore_rejects_invalid_embedding_configuration_before_any_change(
    db_session, tmp_path, restore_target, knowledge_restore_target, monkeypatch
):
    _task(db_session)
    result = _complete_backup(tmp_path)
    monkeypatch.setenv("DECK_KB_EMBEDDING_PROVIDER", "ollama")
    with pytest.raises(OperationError) as error:
        _restore_complete(tmp_path, result.name, restore_target, knowledge_restore_target)
    assert error.value.code == "knowledge_embedding_configuration_invalid"
    for url in (restore_target, knowledge_restore_target):
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT to_regclass('public.tasks')")) is None
                assert connection.scalar(text("SELECT to_regnamespace('deck_kb')")) is None
        finally:
            engine.dispose()


def test_restore_accepts_matching_real_spec(
    db_session, tmp_path, restore_target, knowledge_restore_target, knowledge_store
):
    real = EmbeddingSpec("ollama", "qwen3-embedding:0.6b", 1024)
    use_contract(knowledge_store.engine, real)
    _task(db_session)
    result = _complete_backup(tmp_path)
    _restore_complete(
        tmp_path, result.name, restore_target, knowledge_restore_target, embedding_spec=real
    )
    engine = create_engine(knowledge_restore_target)
    try:
        assert contract_row(engine) == ("deck_kb.v1", "qwen3-embedding:0.6b", 1024)
    finally:
        engine.dispose()


@pytest.mark.parametrize("incomplete", ["empty", "missing_knowledge", "corrupt_knowledge"])
def test_restic_never_copies_an_incomplete_set(db_session, tmp_path, monkeypatch, incomplete):
    from app.operations import restic

    root, runs, credentials = (tmp_path / value for value in ("backups", "runs", "credentials"))
    for directory in (root, runs, credentials):
        directory.mkdir(mode=0o700)
    if incomplete != "empty":
        result = _complete_backup(tmp_path)
        artifact = root / result.name / "knowledge.dump"
        if incomplete == "missing_knowledge":
            artifact.unlink()
        else:
            with artifact.open("ab") as stream:
                stream.write(b"corrupt")
    password = credentials / "password"
    password.write_text("synthetic-restic-value")
    password.chmod(0o600)
    monkeypatch.setattr(restic, "run_command", lambda *a, **k: pytest.fail("No copy"))
    with pytest.raises(OperationError):
        copy_with_restic(
            get_test_database_url(),
            backup_root=root,
            runs_root=runs,
            configuration={"RESTIC_REPOSITORY": "fake-only", "RESTIC_PASSWORD_FILE": str(password)},
        )
    phases = db_session.scalars(
        select(OperationEvent.phase)
        .where(OperationEvent.detail["reason"].astext.in_(["restic_copy", "restic_failed"]))
        .order_by(OperationEvent.id)
    ).all()
    assert phases == ["started", "failed"]


@pytest.mark.parametrize("scope", ["central", "knowledge"])
def test_backup_never_ignores_user_schemas_beginning_with_pg(db_session, tmp_path, scope):
    url = get_test_database_url() if scope == "central" else get_kb_test_database_url()
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA pgdata"))
            connection.execute(text("CREATE TABLE pgdata.kept (id integer)"))
            connection.execute(text("INSERT INTO pgdata.kept VALUES (42)"))
        with pytest.raises(OperationError) as error:
            _complete_backup(tmp_path)
        assert error.value.code == "unsupported_database_schema"
        assert not list((tmp_path / "backups").glob("backup-*"))
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT id FROM pgdata.kept")) == 42
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE pgdata.kept"))
            connection.execute(text("DROP SCHEMA pgdata"))
        engine.dispose()


@pytest.mark.parametrize("scope", ["central", "knowledge"])
def test_restore_never_treats_user_pg_schema_as_empty(
    db_session, tmp_path, restore_target, knowledge_restore_target, monkeypatch, scope
):
    from app.operations import restore

    engine = create_engine(restore_target if scope == "central" else knowledge_restore_target)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA pgdata"))
            connection.execute(text("CREATE TABLE pgdata.kept (id integer)"))
            connection.execute(text("INSERT INTO pgdata.kept VALUES (42)"))
        monkeypatch.setattr(restore, "run_command", lambda *a, **k: pytest.fail("No process"))
        with pytest.raises(OperationError) as error:
            _restore_complete(
                tmp_path, "backup-" + uuid4().hex, restore_target, knowledge_restore_target
            )
        assert error.value.code == "restore_target_not_empty"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT id FROM pgdata.kept")) == 42
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname='public'"))
                == 1
            )
    finally:
        engine.dispose()
