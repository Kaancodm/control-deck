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
from app.kb.contracts import EmbeddingSpec, KnowledgeError
from app.kb.ollama_embeddings import OllamaEmbeddings
from app.kb.repository import KnowledgeStore
from app.workers.lanes.ollama_http import OllamaHTTPClient
from app.workers.lanes.ollama_keep_alive import parse_keep_alive_value
from tests.support.kb import contract_row, use_contract, use_fake_contract
from tests.support.ollama_stub import OllamaStub


@pytest.fixture(scope="module")
def kb_engine():
    if not os.environ.get("DECK_KB_TEST_DATABASE_URL"):
        pytest.skip("DECK_KB_TEST_DATABASE_URL is required for pgvector integration")
    command.upgrade(Config("alembic_kb.ini"), "head")
    engine = create_engine(get_kb_test_database_url())
    use_fake_contract(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def knowledge_store(kb_engine, tmp_path, monkeypatch):
    blocklist = tmp_path / "knowledge-blocklist.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))
    use_fake_contract(kb_engine)
    store = KnowledgeStore(kb_engine)
    yield store
    with kb_engine.begin() as connection:
        connection.execute(text("TRUNCATE deck_kb.chunks, deck_kb.documents"))
    use_fake_contract(kb_engine)


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

    monkeypatch.setattr(knowledge_store.embeddings, "embed_documents", forbidden)
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
    use_fake_contract(knowledge_store.engine)
    knowledge_store.check_contract(write=True)
    command.check(configuration)


def test_cloud_knowledge_requires_current_blocklist(knowledge_store, monkeypatch):
    monkeypatch.delenv("DECK_BLOCKLIST_PATH", raising=False)
    with pytest.raises(KnowledgeError) as error:
        insert(knowledge_store, DataClass.CLOUD_OK)
    assert error.value.code == "knowledge_privacy_configuration_unavailable"


REAL_SPEC = EmbeddingSpec("ollama", "qwen3-embedding:0.6b", 1024)


def ollama_store(engine, stub, **client_options):
    return KnowledgeStore(
        engine,
        embeddings=OllamaEmbeddings(
            client=OllamaHTTPClient(stub.url, **client_options),
            spec=REAL_SPEC,
            timeout_s=2.0,
            keep_alive=parse_keep_alive_value("server"),
        ),
    )


def counts(engine):
    with engine.connect() as connection:
        return (
            connection.scalar(text("SELECT count(*) FROM deck_kb.documents")),
            connection.scalar(text("SELECT count(*) FROM deck_kb.chunks")),
        )


def embed_requests(stub):
    return [request for request in stub.requests if request["path"] == "/api/embed"]


@pytest.fixture
def real_contract(knowledge_store):
    """Restore the row kb_0002 wrote; the fixture teardown switches back to the fake."""
    use_contract(knowledge_store.engine, REAL_SPEC)
    return knowledge_store.engine


def test_ollama_store_puts_searches_and_resolves_on_real_contract(real_contract):
    content = "Synthetic first chunk " * 100
    with OllamaStub() as stub:
        store = ollama_store(real_contract, stub)
        store.check_contract(write=True)
        first = insert(store, content=content)
        other = insert(store, content="Unrelated synthetic note")
        assert len(embed_requests(stub)) == 2
        assert len(embed_requests(stub)[0]["body"]["input"]) == 2
        hits = store.search(content[:1500], task_class=DataClass.LOCAL_ONLY, limit=2)
        assert [item.reference for item in hits] == [first, other]
        assert embed_requests(stub)[-1]["body"]["input"] == [content[:1500]]
        assert store.resolve((first,), task_class=DataClass.LOCAL_ONLY)[0].text.endswith(content)
    assert contract_row(real_contract) == ("deck_kb.v1", "qwen3-embedding:0.6b", 1024)
    assert counts(real_contract) == (2, 3)


@pytest.mark.parametrize(
    "options,code",
    [
        ({"embed_dimension": 8}, "embedding_dimension_mismatch"),
        ({"status_by_path": {"/api/embed": 404}}, "embedding_model_missing"),
        ({"embed_count_delta": -1}, "embedding_malformed"),
    ],
)
def test_ollama_failures_store_nothing(real_contract, options, code):
    with OllamaStub(**options) as stub:
        with pytest.raises(KnowledgeError) as error:
            insert(ollama_store(real_contract, stub))
    assert error.value.code == code
    assert counts(real_contract) == (0, 0)


def test_stopped_ollama_stores_nothing(real_contract):
    with OllamaStub() as stub:
        store = ollama_store(real_contract, stub)
    with pytest.raises(KnowledgeError) as error:
        insert(store)
    assert error.value.code == "embedding_unavailable"
    with pytest.raises(KnowledgeError) as error:
        store.search("question", task_class=DataClass.LOCAL_ONLY)
    assert error.value.code == "embedding_unavailable"
    assert counts(real_contract) == (0, 0)


def test_secret_never_reaches_ollama(real_contract):
    with OllamaStub() as stub:
        store = ollama_store(real_contract, stub)
        with pytest.raises(KnowledgeError) as error:
            insert(store, content="DECK_OWNER_TOKEN=fake-secret-credential-value")
        assert error.value.code == "secret_detected"
        with pytest.raises(KnowledgeError) as error:
            store.search(
                "DECK_OWNER_TOKEN=fake-secret-credential-value", task_class=DataClass.CLOUD_OK
            )
        assert error.value.code == "secret_detected"
        assert embed_requests(stub) == []
    assert counts(real_contract) == (0, 0)


def test_repeated_put_does_not_embed_again(real_contract):
    document_id, task_id = uuid4(), uuid4()
    kwargs = dict(
        document_id=document_id,
        source_task_id=task_id,
        title="Export",
        content="Reviewed result",
        data_class=DataClass.LOCAL_ONLY,
    )
    with OllamaStub() as stub:
        store = ollama_store(real_contract, stub)
        assert store.put_document(**kwargs) == document_id
        assert store.put_document(**kwargs) == document_id
        assert len(embed_requests(stub)) == 1
        with pytest.raises(KnowledgeError) as error:
            store.put_document(**(kwargs | {"content": "Changed result"}))
        assert error.value.code == "knowledge_export_conflict"
        assert len(embed_requests(stub)) == 1
    assert counts(real_contract) == (1, 1)


def test_wrong_contract_never_contacts_ollama(knowledge_store):
    with OllamaStub() as stub:
        with pytest.raises(KnowledgeError) as error:
            insert(ollama_store(knowledge_store.engine, stub))
        assert error.value.code == "knowledge_contract_mismatch"
        assert stub.requests == []


def test_fake_against_real_contract_row_is_a_mismatch(real_contract):
    store = KnowledgeStore(real_contract)
    for operation in (
        lambda: store.check_contract(),
        lambda: insert(store),
        lambda: store.search("question", task_class=DataClass.LOCAL_ONLY),
    ):
        with pytest.raises(KnowledgeError) as error:
            operation()
        assert error.value.code == "knowledge_contract_mismatch"
    assert counts(real_contract) == (0, 0)
