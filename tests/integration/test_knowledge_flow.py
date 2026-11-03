"""Owner decisions, exact snapshots and retryable cross-database exports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.schemas import DataClass
from app.db.models import Event, Task
from app.db.session import get_engine
from app.kb.contracts import KnowledgeError, digest_text
from app.kb.export import export_task
from app.kb.repository import KnowledgeStore
from app.kb.state import ContextSnapshot, KnowledgeExport
from tests.integration.test_api import api_client as api_client
from tests.integration.test_api import owner_token as owner_token
from tests.integration.test_api import worker_token as worker_token
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.integration.test_knowledge_store import knowledge_store as knowledge_store
from tests.integration.test_runner_api import _claim, _identity, _owner, _result, _worker


@pytest.fixture(autouse=True)
def knowledge_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("DECK_KB_PROFILE", "simulation")
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))


def create_context_task(
    client, store, *, classification="local_only", content="Context bytes\r\nunchanged"
):
    document_id = uuid4()
    store.put_document(
        document_id=document_id,
        title="Context",
        content=content,
        data_class=DataClass(classification),
    )
    task = _minimal_task_payload(uuid4())
    task["context_refs"] = [{"source": "knowledge_db", "ref": str(document_id)}]
    task["data_class"] = classification
    if classification != "local_only":
        task["fallback_chain"] = ["claude"]
    response = client.post("/api/v1/tasks", json=task, headers=_owner())
    assert response.status_code == 201, response.text
    return task["task_id"]


def approve(client, task_id):
    response = client.post(f"/api/v1/tasks/{task_id}/approve", headers=_owner())
    assert response.status_code == 200, response.text


def finish(client, task_id, text="Reviewed useful knowledge", *, review=True):
    approve(client, task_id)
    envelope = _claim(client)
    response = client.post("/api/v1/worker/start", json=_identity(envelope), headers=_worker())
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/v1/worker/result",
        headers=_worker(),
        json={"lease_id": envelope["lease"]["lease_id"], "result": _result(envelope, output=text)},
    )
    assert response.status_code == 200, response.text
    if review:
        response = client.post(
            f"/api/v1/tasks/{task_id}/review", json={"approved": True}, headers=_owner()
        )
        assert response.status_code == 200, response.text


def test_approved_snapshot_is_used_when_external_knowledge_is_unavailable(
    api_client, knowledge_store, monkeypatch
):
    original = "Exact context e\u0301\r\nsecond line"
    task_id = create_context_task(api_client, knowledge_store, content=original)
    monkeypatch.setenv("DECK_KB_PROFILE", "disabled")
    approve(api_client, task_id)
    envelope = _claim(api_client)
    assert original in envelope["payload_text"]
    with Session(get_engine(for_tests=True)) as session:
        approval = session.scalar(
            select(Event).where(Event.task_id == task_id, Event.event_type == "task.approve")
        )
        snapshot = session.scalar(select(ContextSnapshot).where(ContextSnapshot.task_id == task_id))
        assert approval.detail["context_hashes"][0]["sha256"] == snapshot.content_sha256


def test_secret_context_is_never_stored_in_host_database(api_client, knowledge_store, monkeypatch):
    task_id = uuid4()
    from app.kb.contracts import ContextBlock, digest_text

    raw = "DECK_WORKER_TOKEN=fake-credential-secret-value"
    reference = str(uuid4())
    block = ContextBlock(reference, DataClass.LOCAL_ONLY, raw, digest_text(raw))
    monkeypatch.setattr(KnowledgeStore, "resolve", lambda *args, **kwargs: (block,))
    payload = _minimal_task_payload(task_id)
    payload["context_refs"] = [{"source": "knowledge_db", "ref": reference}]
    response = api_client.post("/api/v1/tasks", json=payload, headers=_owner())
    assert response.status_code == 422
    assert raw not in response.text
    with Session(get_engine(for_tests=True)) as session:
        assert session.get(Task, task_id) is None
        assert session.scalar(select(func.count()).select_from(ContextSnapshot)) == 0


def test_cloud_redacted_context_uses_same_privacy_pipeline(api_client, knowledge_store):
    original = "synthetic" + "@example.invalid"
    task_id = create_context_task(
        api_client, knowledge_store, classification="cloud_redacted", content=original
    )
    approve(api_client, task_id)
    envelope = _claim(api_client, "claude")
    assert original not in str(envelope)
    assert "[[CD_" in envelope["payload_text"]


def test_local_context_cannot_be_submitted_for_cloud(api_client, knowledge_store):
    document_id = uuid4()
    knowledge_store.put_document(
        document_id=document_id,
        title="Local",
        content="Private bounded data",
        data_class=DataClass.LOCAL_ONLY,
    )
    payload = _minimal_task_payload(uuid4())
    payload.update(
        data_class="cloud_ok",
        fallback_chain=["claude"],
        context_refs=[{"source": "knowledge_db", "ref": str(document_id)}],
    )
    response = api_client.post("/api/v1/tasks", json=payload, headers=_owner())
    assert response.status_code == 422


def test_context_snapshot_tampering_blocks_claim(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    approve(api_client, task_id)
    with Session(get_engine(for_tests=True)) as session:
        snapshot = session.scalar(select(ContextSnapshot).where(ContextSnapshot.task_id == task_id))
        replacement = ContextSnapshot(
            task_id=snapshot.task_id,
            position=snapshot.position,
            reference=snapshot.reference,
            data_class=snapshot.data_class,
            content="Changed after approval",
            content_sha256=digest_text("Changed after approval"),
        )
        session.delete(snapshot)
        session.flush()
        session.add(replacement)
        session.commit()
    response = api_client.post("/api/v1/worker/lease", json={"worker": "ollama"}, headers=_worker())
    assert response.status_code == 404
    with Session(get_engine(for_tests=True)) as session:
        assert session.get(Task, task_id).status.value == "blocked"


def test_context_update_is_rejected_by_database(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    with pytest.raises(DBAPIError):
        with Session(get_engine(for_tests=True)) as session:
            session.execute(
                text("UPDATE task_context_snapshots SET content = 'changed' WHERE task_id = :id"),
                {"id": task_id},
            )
            session.commit()


def test_no_export_without_separate_owner_request(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id)
    with Session(get_engine(for_tests=True)) as session:
        assert session.get(KnowledgeExport, task_id) is None
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "kb.export")
            )
            == 0
        )


def test_export_requires_explicit_class_and_owner(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id)
    endpoint = f"/api/v1/tasks/{task_id}/export"
    assert api_client.post(endpoint, json={}, headers=_owner()).status_code == 422
    assert (
        api_client.post(endpoint, json={"data_class": "local_only"}, headers=_worker()).status_code
        == 401
    )
    assert (
        api_client.post(endpoint, json={"data_class": "cloud_ok"}, headers=_owner()).status_code
        == 409
    )


def test_export_review_state_is_rejected(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id, review=False)
    response = api_client.post(
        f"/api/v1/tasks/{task_id}/export", json={"data_class": "local_only"}, headers=_owner()
    )
    assert response.status_code == 409


def test_explicit_export_roundtrip_is_idempotent(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id, text="Exported e\u0301\r\nexact bytes")
    endpoint = f"/api/v1/tasks/{task_id}/export"
    first = api_client.post(endpoint, json={"data_class": "local_only"}, headers=_owner())
    second = api_client.post(endpoint, json={"data_class": "local_only"}, headers=_owner())
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    blocks = knowledge_store.resolve(
        (first.json()["document_id"],), task_class=DataClass.LOCAL_ONLY
    )
    assert blocks[0].text.endswith("Exported e\u0301\r\nexact bytes")
    with Session(get_engine(for_tests=True)) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.event_type == "kb.export")
            )
            == 1
        )


@pytest.mark.parametrize("fail_after_write", [False, True])
def test_partial_export_can_resume_without_duplicate_document(
    api_client, knowledge_store, monkeypatch, fail_after_write
):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id)
    original = knowledge_store.put_document
    calls = 0

    def interrupted(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if fail_after_write:
                original(**kwargs)
            raise KnowledgeError("knowledge_write_unavailable")
        return original(**kwargs)

    monkeypatch.setattr(knowledge_store, "put_document", interrupted)
    with Session(get_engine(for_tests=True)) as session:
        with pytest.raises(KnowledgeError):
            export_task(
                session, UUID(task_id), data_class=DataClass.LOCAL_ONLY, store=knowledge_store
            )
        assert session.get(KnowledgeExport, task_id).state == "pending"
        session.rollback()
        receipt = export_task(
            session, UUID(task_id), data_class=DataClass.LOCAL_ONLY, store=knowledge_store
        )
        assert receipt.state == "completed"
    with knowledge_store.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM deck_kb.documents WHERE source_task_id = :id"),
                {"id": task_id},
            )
            == 1
        )


def test_external_export_runs_without_host_transaction(api_client, knowledge_store, monkeypatch):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id)
    original = knowledge_store.put_document
    with Session(get_engine(for_tests=True)) as session:

        def checked(**kwargs):
            assert not session.in_transaction()
            return original(**kwargs)

        monkeypatch.setattr(knowledge_store, "put_document", checked)
        assert (
            export_task(
                session, UUID(task_id), data_class=DataClass.LOCAL_ONLY, store=knowledge_store
            ).state
            == "completed"
        )


def test_parallel_owner_exports_write_one_document_and_audit(api_client, knowledge_store):
    task_id = create_context_task(api_client, knowledge_store)
    finish(api_client, task_id)

    def run_export(_):
        with Session(get_engine(for_tests=True)) as session:
            return export_task(
                session, UUID(task_id), data_class=DataClass.LOCAL_ONLY, store=knowledge_store
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(run_export, range(2))
    assert first == second
    with Session(get_engine(for_tests=True)) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.task_id == task_id, Event.event_type == "kb.export")
            )
            == 1
        )
    with knowledge_store.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM deck_kb.documents WHERE source_task_id = :id"),
                {"id": task_id},
            )
            == 1
        )


def test_snapshot_migration_refuses_to_discard_owner_context(api_client, knowledge_store):
    import argparse

    from alembic.config import Config

    from alembic import command

    task_id = create_context_task(api_client, knowledge_store)
    configuration = Config("alembic.ini")
    configuration.cmd_opts = argparse.Namespace(x=["target=test"])
    with pytest.raises(RuntimeError, match="knowledge snapshots"):
        command.downgrade(configuration, "20251103_0012")
    with Session(get_engine(for_tests=True)) as session:
        assert (
            session.scalar(select(ContextSnapshot).where(ContextSnapshot.task_id == task_id))
            is not None
        )


def test_snapshot_migration_roundtrip_preserves_independent_knowledge(db_session, knowledge_store):
    import argparse

    from alembic.config import Config

    from alembic import command

    document_id = uuid4()
    knowledge_store.put_document(
        document_id=document_id,
        title="Retained knowledge",
        content="Independent knowledge survives the host migration",
        data_class=DataClass.LOCAL_ONLY,
    )
    configuration = Config("alembic.ini")
    configuration.cmd_opts = argparse.Namespace(x=["target=test"])
    command.downgrade(configuration, "20251103_0012")
    try:
        assert db_session.scalar(text("SELECT to_regclass('task_context_snapshots')")) is None
        db_session.rollback()
    finally:
        command.upgrade(configuration, "head")
    assert knowledge_store.resolve((str(document_id),), task_class=DataClass.LOCAL_ONLY)
    command.check(configuration)
