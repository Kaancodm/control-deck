"""Real pgvector queries against a separate disposable, migrated database."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.core.schemas import DataClass
from app.kb.config import get_kb_test_database_url
from app.kb.contracts import KnowledgeError
from app.kb.repository import KnowledgeStore


@pytest.fixture(scope="module")
def kb_engine():
    if not os.environ.get("DECK_KB_TEST_DATABASE_URL"):
        pytest.skip("DECK_KB_TEST_DATABASE_URL is required for pgvector integration")
    command.upgrade(Config("alembic_kb.ini"), "head")
    engine = create_engine(get_kb_test_database_url())
    yield engine
    engine.dispose()


@pytest.fixture
def knowledge_store(kb_engine, tmp_path, monkeypatch):
    blocklist = tmp_path / "knowledge-blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    store = KnowledgeStore(kb_engine)
    yield store
    with kb_engine.begin() as connection:
        connection.execute(text("TRUNCATE deck_kb.chunks, deck_kb.documents"))
        connection.execute(
            text(
                "UPDATE deck_kb.contract SET version = 'deck_kb.v1', "
                "embedding_model = 'deck-fake-embedding-v1', embedding_dimension = 8"
            )
        )


def insert(store, classification=DataClass.LOCAL_ONLY, content="Useful bounded knowledge"):
    document_id = uuid4()
    store.put_document(
        document_id=document_id, title="Example", content=content, data_class=classification
    )
    return str(document_id)


@pytest.mark.parametrize("task_class", list(DataClass))
@pytest.mark.parametrize("document_class", list(DataClass))
def test_sql_class_filter_for_explicit_references(knowledge_store, task_class, document_class):
    reference = insert(knowledge_store, document_class)
    allowed = list(DataClass).index(document_class) >= list(DataClass).index(task_class)
    if allowed:
        assert (
            knowledge_store.resolve((reference,), task_class=task_class)[0].data_class
            == document_class
        )
    else:
        with pytest.raises(KnowledgeError) as error:
            knowledge_store.resolve((reference,), task_class=task_class)
        assert error.value.code == "context_reference_unavailable"


def test_search_filters_before_ranking(knowledge_store):
    query = "Exact private match"
    local = insert(knowledge_store, DataClass.LOCAL_ONLY, query)
    public = insert(knowledge_store, DataClass.CLOUD_OK, "Different public knowledge")
    hits = knowledge_store.search(query, task_class=DataClass.CLOUD_OK, limit=1)
    assert [item.reference for item in hits] == [public]
    assert all(item.reference != local and query not in item.text for item in hits)


def test_missing_reference_never_silently_dropped(knowledge_store):
    existing = insert(knowledge_store)
    with pytest.raises(KnowledgeError) as error:
        knowledge_store.resolve((existing, str(uuid4())), task_class=DataClass.LOCAL_ONLY)
    assert error.value.code == "context_reference_unavailable"


def test_roundtrip_preserves_unicode_and_line_endings(knowledge_store):
    original = ("e\u0301\r\n🚀\t" * 500) + "end\n"
    reference = insert(knowledge_store, content=original)
    block = knowledge_store.resolve((reference,), task_class=DataClass.LOCAL_ONLY)[0]
    assert block.text.endswith(original)
    block.validate(DataClass.LOCAL_ONLY)


def test_duplicate_exports_are_idempotent_and_conflicts_rejected(knowledge_store):
    document_id, task_id = uuid4(), uuid4()
    kwargs = dict(
        document_id=document_id,
        source_task_id=task_id,
        title="Export",
        content="Reviewed result",
        data_class=DataClass.LOCAL_ONLY,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert (
            list(executor.map(lambda _: knowledge_store.put_document(**kwargs), range(2)))
            == [document_id] * 2
        )
    with knowledge_store.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM deck_kb.documents")) == 1
        assert connection.scalar(text("SELECT count(*) FROM deck_kb.chunks")) == 1
    with pytest.raises(KnowledgeError) as error:
        knowledge_store.put_document(**(kwargs | {"data_class": DataClass.CLOUD_OK}))
    assert error.value.code == "knowledge_export_conflict"


def test_secret_rejected_before_embedding_and_storage(knowledge_store, monkeypatch):
    def forbidden(_):
        pytest.fail("Embedding must not see a secret")

    monkeypatch.setattr(knowledge_store.embeddings, "embed", forbidden)
    with pytest.raises(KnowledgeError) as error:
        insert(knowledge_store, content="DECK_OWNER_TOKEN=fake-secret-credential-value")
    assert error.value.code == "secret_detected"
    with knowledge_store.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM deck_kb.documents")) == 0


@pytest.mark.parametrize(
    "column,value",
    [("version", "unknown"), ("embedding_model", "other"), ("embedding_dimension", 3)],
)
def test_contract_mismatch_fails_closed(knowledge_store, column, value):
    with knowledge_store.engine.begin() as connection:
        connection.execute(text(f"UPDATE deck_kb.contract SET {column} = :value"), {"value": value})
    with pytest.raises(KnowledgeError) as error:
        knowledge_store.check_contract()
    assert error.value.code == "knowledge_contract_mismatch"


def test_document_data_class_has_no_database_default(knowledge_store):
    with pytest.raises(DBAPIError):
        with knowledge_store.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deck_kb.documents(id, title, content_sha256) "
                    "VALUES (:id, 'Missing class', :digest)"
                ),
                {"id": uuid4(), "digest": "a" * 64},
            )


def test_documents_and_chunks_cannot_be_mutated(knowledge_store):
    insert(knowledge_store)
    for sql in ("UPDATE deck_kb.documents SET title = 'Changed'", "DELETE FROM deck_kb.chunks"):
        with pytest.raises(DBAPIError):
            with knowledge_store.engine.begin() as connection:
                connection.execute(text(sql))


def test_migration_metadata_matches(knowledge_store):
    command.check(Config("alembic_kb.ini"))


def test_contract_rejects_removed_immutability_guard(knowledge_store):
    with knowledge_store.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE deck_kb.documents DISABLE TRIGGER immutable_documents")
        )
    try:
        with pytest.raises(KnowledgeError) as error:
            knowledge_store.check_contract()
        assert error.value.code == "knowledge_contract_mismatch"
    finally:
        with knowledge_store.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE deck_kb.documents ENABLE TRIGGER immutable_documents")
            )


def test_contract_rejects_implicit_class_default(knowledge_store):
    with knowledge_store.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE deck_kb.documents ALTER COLUMN data_class SET DEFAULT 'local_only'")
        )
    try:
        with pytest.raises(KnowledgeError) as error:
            knowledge_store.check_contract()
        assert error.value.code == "knowledge_contract_mismatch"
    finally:
        with knowledge_store.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE deck_kb.documents ALTER COLUMN data_class DROP DEFAULT")
            )


def test_knowledge_migration_roundtrip(knowledge_store):
    configuration = Config("alembic_kb.ini")
    command.downgrade(configuration, "base")
    try:
        with knowledge_store.engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('deck_kb.documents')")) is None
    finally:
        command.upgrade(configuration, "head")
    knowledge_store.check_contract(write=True)
    command.check(configuration)


def test_cloud_knowledge_requires_current_blocklist(knowledge_store, monkeypatch):
    monkeypatch.delenv("DECK_BLOCKLIST_PATH", raising=False)
    with pytest.raises(KnowledgeError) as error:
        insert(knowledge_store, DataClass.CLOUD_OK)
    assert error.value.code == "knowledge_privacy_configuration_unavailable"
