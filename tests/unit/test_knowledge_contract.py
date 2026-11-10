"""Privacy boundaries for explicit classes, exact context sets and local vectors."""

from uuid import uuid4

import pytest

from app.core.schemas import DataClass
from app.kb.config import get_kb_test_database_url, load_kb_settings
from app.kb.contracts import (
    SCHEMA_EMBEDDING_DIMENSION,
    ContextBlock,
    EmbeddingSpec,
    KnowledgeError,
    allowed_classes,
    digest_text,
    validate_contexts,
)
from app.kb.embeddings import FakeLocalEmbeddings, validate_vector, vector_literal


@pytest.mark.parametrize("task_class", list(DataClass))
@pytest.mark.parametrize("document_class", list(DataClass))
def test_class_matrix(task_class, document_class):
    classes = list(DataClass)
    # The enum is ordered from the most restricted class to the least restricted.
    expected = classes.index(document_class) >= classes.index(task_class)
    assert (document_class in allowed_classes(task_class)) is expected


def block(text="Local knowledge", classification=DataClass.LOCAL_ONLY):
    return ContextBlock(str(uuid4()), classification, text, digest_text(text))


def test_context_set_requires_exact_order_and_membership():
    first, second = block(), block()
    with pytest.raises(KnowledgeError, match="Knowledge") as error:
        validate_contexts(
            (second, first),
            references=(first.reference, second.reference),
            task_class=DataClass.LOCAL_ONLY,
        )
    assert error.value.code == "context_set_mismatch"


def test_context_digest_changes_for_line_endings_and_unicode():
    original = block("first\r\nsecond e\u0301")
    changed = ContextBlock(
        original.reference, original.data_class, "first\nsecond é", original.sha256
    )
    with pytest.raises(KnowledgeError) as error:
        changed.validate(DataClass.LOCAL_ONLY)
    assert error.value.code == "context_hash_mismatch"


def test_restricted_context_is_never_accepted_for_cloud():
    with pytest.raises(KnowledgeError) as error:
        block().validate(DataClass.CLOUD_REDACTED)
    assert error.value.code == "context_class_forbidden"


def test_duplicate_references_rejected():
    item = block()
    with pytest.raises(KnowledgeError) as error:
        validate_contexts(
            (item, item),
            references=(item.reference, item.reference),
            task_class=DataClass.LOCAL_ONLY,
        )
    assert error.value.code == "invalid_context_references"


def test_snapshot_repr_contains_no_text():
    assert "private content" not in repr(block("private content"))


@pytest.mark.parametrize(
    "vector,code",
    [
        ((0.0,) * 8, "embedding_invalid"),
        ((float("nan"),) * 8, "embedding_invalid"),
        ((float("inf"),) * 8, "embedding_invalid"),
        ((True,) * 8, "embedding_invalid"),
        (("0.5",) * 8, "embedding_invalid"),
        ((10**400,) * 8, "embedding_invalid"),
        ((0.5,) * 7, "embedding_dimension_mismatch"),
    ],
)
def test_bad_embedding_rejected(vector, code):
    with pytest.raises(KnowledgeError) as error:
        validate_vector(vector, dimension=8)
    assert error.value.code == code


def test_fake_embedding_is_explicit_and_deterministic():
    provider = FakeLocalEmbeddings()
    assert provider.spec == EmbeddingSpec("fake", "deck-fake-embedding-v2", 1024)
    assert provider.spec.dimension == SCHEMA_EMBEDDING_DIMENSION == 1024
    first = provider.embed_query("bounded text")
    assert first == provider.embed_query("bounded text")
    assert provider.embed_documents(["bounded text", "other text"])[0] == first
    assert provider.embed_documents(["other text"])[0] != first
    assert len(first) == 1024
    assert all(0 < value <= 1 for value in first)
    assert vector_literal(first, dimension=1024).count(",") == 1023


@pytest.mark.parametrize("texts", [[], [""], ["x"] * 17, "plain string", [b"bytes"]])
def test_fake_embedding_rejects_unbounded_batches(texts):
    with pytest.raises(KnowledgeError) as error:
        FakeLocalEmbeddings().embed_documents(texts)
    assert error.value.code == "embedding_input_invalid"


def test_vector_literal_checks_requested_dimension():
    with pytest.raises(KnowledgeError) as error:
        vector_literal((0.5,) * 8, dimension=1024)
    assert error.value.code == "embedding_dimension_mismatch"


@pytest.mark.parametrize("profile", ["disabled", "live", "", "true", "Runtime", "prod"])
def test_unknown_profiles_fail_before_connecting(monkeypatch, profile):
    monkeypatch.setenv("DECK_KB_PROFILE", profile)
    with pytest.raises(KnowledgeError) as error:
        load_kb_settings()
    assert error.value.code == "knowledge_live_profile_unavailable"


@pytest.mark.parametrize("url", ["", "invalid", "sqlite:///kb_test", "postgresql+psycopg:///kb"])
def test_simulation_requires_explicit_test_database(monkeypatch, url):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", url)
    with pytest.raises(KnowledgeError) as error:
        get_kb_test_database_url()
    assert error.value.code == "knowledge_test_database_required"
