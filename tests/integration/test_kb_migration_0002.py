"""kb_0002 widens only an empty, unchanged knowledge contract, in both directions."""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy import Engine, create_engine, pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from alembic import command

LEGACY = ("deck_kb.v1", "deck-fake-embedding-v1", 8)
REAL = ("deck_kb.v1", "qwen3-embedding:0.6b", 1024)


def _administration_url() -> str:
    raw = os.environ.get("DECK_KB_TEST_DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("DECK_KB_TEST_DATABASE_URL is required for pgvector integration")
    assert make_url(raw).database.endswith("_test")
    return raw


def _administer(administration_url: str, statement: str) -> None:
    engine = create_engine(administration_url, poolclass=pool.NullPool)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture
def disposable(monkeypatch) -> Iterator[Engine]:
    administration_url = _administration_url()
    source = make_url(administration_url)
    name = "deck_kb_0002_" + uuid4().hex + "_test"
    _administer(administration_url, f'CREATE DATABASE "{name}"')
    url = source.set(database=name).render_as_string(hide_password=False)
    monkeypatch.delenv("DECK_KB_DATABASE_URL", raising=False)
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", url)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        _administer(administration_url, f'DROP DATABASE "{name}" WITH (FORCE)')


def _config() -> Config:
    return Config("alembic_kb.ini")


def _state(engine: Engine) -> tuple[str, str, tuple[str, str, int], int, int]:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_kb_version"))
        column = connection.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'deck_kb.chunks'::regclass AND attname = 'embedding'"
            )
        )
        contract = tuple(
            connection.execute(
                text("SELECT version, embedding_model, embedding_dimension FROM deck_kb.contract")
            ).one()
        )
        documents = connection.scalar(text("SELECT count(*) FROM deck_kb.documents"))
        chunks = connection.scalar(text("SELECT count(*) FROM deck_kb.chunks"))
    return revision, column, contract, documents, chunks


def _active_triggers(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O' "
                "AND tgname IN ('immutable_documents', 'immutable_chunks')"
            )
        )


def _insert(engine: Engine, dimension: int) -> None:
    content = "Synthetic immutable chunk"
    digest = hashlib.sha256(content.encode()).hexdigest()
    document_id = uuid4()
    vector = "[" + ",".join(["1"] + ["0"] * (dimension - 1)) + "]"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO deck_kb.documents (id, title, data_class, content_sha256) "
                "VALUES (:id, 'Synthetic', 'local_only', :digest)"
            ),
            {"id": document_id, "digest": digest},
        )
        connection.execute(
            text(
                "INSERT INTO deck_kb.chunks "
                "(id, document_id, position, content, content_sha256, embedding) "
                "VALUES (:id, :document, 0, :content, :digest, CAST(:vector AS vector))"
            ),
            {
                "id": uuid4(),
                "document": document_id,
                "content": content,
                "digest": digest,
                "vector": vector,
            },
        )


def test_upgrade_widens_empty_contract_and_keeps_immutability(disposable):
    command.upgrade(_config(), "head")
    assert _state(disposable) == ("kb_0002", "vector(1024)", REAL, 0, 0)
    assert _active_triggers(disposable) == 2
    command.check(_config())
    with disposable.connect() as connection:
        nullable = connection.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'deck_kb' AND table_name = 'chunks' "
                "AND column_name = 'embedding'"
            )
        )
        assert nullable == "NO"
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'deck_kb' "
                    "AND tablename = 'chunks' AND indexdef ILIKE '%embedding%'"
                )
            )
            == 0
        )
    _insert(disposable, 1024)
    for statement in (
        "UPDATE deck_kb.documents SET title = 'Changed'",
        "UPDATE deck_kb.chunks SET position = 1",
        "DELETE FROM deck_kb.chunks",
        "DELETE FROM deck_kb.documents",
    ):
        with pytest.raises(DBAPIError):
            with disposable.begin() as connection:
                connection.execute(text(statement))
    assert _state(disposable)[3:] == (1, 1)


def test_downgrade_restores_legacy_contract_and_repeats(disposable):
    for _ in range(2):
        command.upgrade(_config(), "head")
        assert _state(disposable) == ("kb_0002", "vector(1024)", REAL, 0, 0)
        command.downgrade(_config(), "kb_0001")
        assert _state(disposable) == ("kb_0001", "vector(8)", LEGACY, 0, 0)
        assert _active_triggers(disposable) == 2
    command.upgrade(_config(), "head")
    command.check(_config())


def test_upgrade_refuses_stored_knowledge_and_changes_nothing(disposable):
    command.upgrade(_config(), "kb_0001")
    _insert(disposable, 8)
    before = _state(disposable)
    with pytest.raises(DBAPIError, match="knowledge_tables_not_empty"):
        command.upgrade(_config(), "head")
    assert before == ("kb_0001", "vector(8)", LEGACY, 1, 1)
    assert _state(disposable) == before
    assert _active_triggers(disposable) == 2


def test_downgrade_refuses_stored_knowledge_and_changes_nothing(disposable):
    command.upgrade(_config(), "head")
    _insert(disposable, 1024)
    before = _state(disposable)
    with pytest.raises(DBAPIError, match="knowledge_tables_not_empty"):
        command.downgrade(_config(), "kb_0001")
    assert before == ("kb_0002", "vector(1024)", REAL, 1, 1)
    assert _state(disposable) == before


@pytest.mark.parametrize(
    "assignment",
    [
        "embedding_model = 'unexpected-model:1b'",
        "embedding_dimension = 9",
        "version = 'deck_kb.v0'",
    ],
)
def test_upgrade_refuses_foreign_contract_row(disposable, assignment):
    command.upgrade(_config(), "kb_0001")
    with disposable.begin() as connection:
        connection.execute(text(f"UPDATE deck_kb.contract SET {assignment}"))
    before = _state(disposable)
    with pytest.raises(DBAPIError, match="knowledge_contract_unexpected"):
        command.upgrade(_config(), "head")
    assert _state(disposable) == before
    assert before[:2] == ("kb_0001", "vector(8)")


def test_offline_script_carries_the_guard(disposable):
    buffer = io.StringIO()
    try:
        command.upgrade(Config("alembic_kb.ini", output_buffer=buffer), "kb_0001:kb_0002", sql=True)
    except CommandError:
        pytest.fail("Offline generation must not need a connection")
    script = buffer.getvalue()
    assert "LOCK TABLE deck_kb.contract, deck_kb.documents, deck_kb.chunks" in script
    assert "knowledge_tables_not_empty" in script
    assert "vector(1024)" in script
    assert script.index("knowledge_tables_not_empty") < script.index("ALTER TABLE")
