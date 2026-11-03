"""Privacy boundaries for explicit classes, exact context sets and local vectors."""

from uuid import uuid4

import pytest

from app.core.schemas import DataClass
from app.kb.config import get_kb_test_database_url, require_simulation
from app.kb.contracts import (
    ContextBlock,
    KnowledgeError,
    allowed_classes,
    digest_text,
    validate_contexts,
)
from app.kb.embeddings import FakeLocalEmbeddings, validate_vector


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
    "vector", [(0.0,) * 8, (float("nan"),) * 8, (float("inf"),) * 8, (True,) * 8, (0.5,) * 7]
)
def test_bad_embedding_rejected(vector):
    with pytest.raises(KnowledgeError):
        validate_vector(vector, dimension=8)


def test_fake_embedding_is_explicit_and_deterministic():
    provider = FakeLocalEmbeddings()
    assert provider.model == "deck-fake-embedding-v1"
    assert provider.embed("bounded text") == provider.embed("bounded text")
    assert len(provider.embed("bounded text")) == 8


@pytest.mark.parametrize("profile", ["disabled", "live", "", "true"])
def test_live_profiles_fail_before_connecting(monkeypatch, profile):
    monkeypatch.setenv("DECK_KB_PROFILE", profile)
    with pytest.raises(KnowledgeError) as error:
        require_simulation()
    assert error.value.code == "knowledge_live_profile_unavailable"


@pytest.mark.parametrize("url", ["", "invalid", "sqlite:///kb_test", "postgresql+psycopg:///kb"])
def test_simulation_requires_explicit_test_database(monkeypatch, url):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", url)
    with pytest.raises(KnowledgeError) as error:
        get_kb_test_database_url()
    assert error.value.code == "knowledge_test_database_required"
