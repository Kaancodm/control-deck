"""Embedding configuration fails closed before any database or HTTP connection."""

from __future__ import annotations

import socket

import pytest

from app.kb.config import load_embedding_settings, load_embedding_spec
from app.kb.contracts import FAKE_EMBEDDING_SPEC, EmbeddingSpec, KnowledgeError
from app.kb.embeddings import FakeLocalEmbeddings
from app.kb.ollama_embeddings import OllamaEmbeddings
from app.kb.repository import build_embeddings, configured_store

URL = "http://127.0.0.1:9"
MODEL = "embed-fixture:0.6b"
OLLAMA = {
    "DECK_KB_EMBEDDING_PROVIDER": "ollama",
    "DECK_KB_EMBEDDING_MODEL": MODEL,
    "DECK_KB_EMBEDDING_DIMENSION": "1024",
    "DECK_OLLAMA_URL": URL,
}


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("Configuration must not open a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_defaults_select_the_fake():
    settings = load_embedding_settings({})
    assert settings.spec == FAKE_EMBEDDING_SPEC
    assert settings.ollama_url is None
    assert settings.timeout_s == 60.0
    assert settings.keep_alive.omit
    assert isinstance(build_embeddings(settings), FakeLocalEmbeddings)


def test_explicit_fake_is_the_same_spec():
    assert load_embedding_spec({"DECK_KB_EMBEDDING_PROVIDER": "fake"}) == FAKE_EMBEDDING_SPEC


def test_ollama_settings_are_complete():
    settings = load_embedding_settings(
        OLLAMA | {"DECK_KB_EMBEDDING_TIMEOUT_S": "85", "DECK_KB_EMBEDDING_KEEP_ALIVE": "0"}
    )
    assert settings.spec == EmbeddingSpec("ollama", MODEL, 1024)
    assert settings.ollama_url == URL
    assert settings.timeout_s == 85.0
    assert settings.keep_alive.wire == "0"
    embeddings = build_embeddings(settings)
    assert isinstance(embeddings, OllamaEmbeddings)
    assert embeddings.spec == settings.spec
    assert URL not in repr(settings) and URL not in repr(embeddings)


def test_namespaced_model_is_accepted():
    spec = load_embedding_spec(OLLAMA | {"DECK_KB_EMBEDDING_MODEL": "library/qwen3:0.6b"})
    assert spec.model == "library/qwen3:0.6b"


@pytest.mark.parametrize(
    "change",
    [
        {"DECK_KB_EMBEDDING_PROVIDER": "openai"},
        {"DECK_KB_EMBEDDING_PROVIDER": "Ollama"},
        {"DECK_KB_EMBEDDING_MODEL": ""},
        {"DECK_KB_EMBEDDING_MODEL": "embed-fixture"},
        {"DECK_KB_EMBEDDING_MODEL": "embed-fixture:cloud"},
        {"DECK_KB_EMBEDDING_MODEL": "embed-fixture:0.6b-cloud"},
        {"DECK_KB_EMBEDDING_MODEL": "embed-fixture:0.6b:local"},
        {"DECK_KB_EMBEDDING_MODEL": "host.example/a/b:1"},
        {"DECK_KB_EMBEDDING_MODEL": "https://host/model:1"},
        {"DECK_KB_EMBEDDING_DIMENSION": ""},
        {"DECK_KB_EMBEDDING_DIMENSION": "8"},
        {"DECK_KB_EMBEDDING_DIMENSION": "1023"},
        {"DECK_KB_EMBEDDING_DIMENSION": "1024.0"},
        {"DECK_KB_EMBEDDING_DIMENSION": "+1024"},
        {"DECK_KB_EMBEDDING_DIMENSION": "١٠٢٤"},
        {"DECK_OLLAMA_URL": ""},
        {"DECK_OLLAMA_URL": "http://localhost:11434"},
        {"DECK_OLLAMA_URL": "http://192.0.2.1:11434"},
        {"DECK_OLLAMA_URL": "https://127.0.0.1:11434"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "0"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "0.5"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "86"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "nan"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "inf"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "sixty"},
        {"DECK_KB_EMBEDDING_KEEP_ALIVE": "forever"},
        {"DECK_KB_EMBEDDING_KEEP_ALIVE": "25h"},
    ],
)
def test_invalid_ollama_configuration_is_rejected_without_values(change):
    environment = OLLAMA | change
    with pytest.raises(KnowledgeError) as error:
        load_embedding_settings(environment)
    assert error.value.code == "knowledge_embedding_configuration_invalid"
    for value in change.values():
        if value:
            assert value not in str(error.value) and value not in repr(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"DECK_KB_EMBEDDING_MODEL": MODEL},
        {"DECK_KB_EMBEDDING_DIMENSION": "1024"},
        {"DECK_KB_EMBEDDING_TIMEOUT_S": "0"},
    ],
)
def test_fake_rejects_extra_identity_or_bad_values(change):
    with pytest.raises(KnowledgeError) as error:
        load_embedding_settings({"DECK_KB_EMBEDDING_PROVIDER": "fake"} | change)
    assert error.value.code == "knowledge_embedding_configuration_invalid"


def test_simulation_store_uses_configured_ollama_spec(monkeypatch):
    monkeypatch.setenv("DECK_KB_PROFILE", "simulation")
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", "postgresql+psycopg:///kb_unit_test")
    for key, value in OLLAMA.items():
        monkeypatch.setenv(key, value)
    store = configured_store()
    try:
        assert isinstance(store.embeddings, OllamaEmbeddings)
        assert store.spec == EmbeddingSpec("ollama", MODEL, 1024)
    finally:
        store.engine.dispose()


def test_invalid_embedding_configuration_wins_before_database_selection(monkeypatch):
    monkeypatch.setenv("DECK_KB_PROFILE", "simulation")
    monkeypatch.delenv("DECK_KB_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DECK_KB_EMBEDDING_PROVIDER", "ollama")
    with pytest.raises(KnowledgeError) as error:
        configured_store()
    assert error.value.code == "knowledge_embedding_configuration_invalid"
