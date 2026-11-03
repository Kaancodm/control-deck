"""Bounded host-side knowledge values and privacy policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

from app.core.schemas import DataClass

CONTRACT_VERSION = "deck_kb.v1"
EMBEDDING_MODEL = "deck-fake-embedding-v1"
EMBEDDING_DIMENSION = 8
MAX_DOCUMENT_BYTES = 24_000
MAX_CONTEXT_BYTES = 96_000
MAX_CONTEXT_REFS = 16


class KnowledgeError(ValueError):
    """A safe reason code without source text, identifiers, or connection details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Knowledge operation could not be completed.")


def allowed_classes(task_class: DataClass) -> tuple[DataClass, ...]:
    """Return the classes that may enter a task, before ranking or loading text."""
    if task_class == DataClass.LOCAL_ONLY:
        return tuple(DataClass)
    if task_class == DataClass.CLOUD_REDACTED:
        return (DataClass.CLOUD_REDACTED, DataClass.CLOUD_OK)
    if task_class == DataClass.CLOUD_OK:
        return (DataClass.CLOUD_OK,)
    raise KnowledgeError("invalid_data_class")


def digest_text(text: str) -> str:
    try:
        return sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise KnowledgeError("invalid_utf8") from None


@dataclass(frozen=True)
class ContextBlock:
    """Immutable local text; never expose this value to an unauthorised lane."""

    reference: str
    data_class: DataClass
    text: str = field(repr=False)
    sha256: str

    def validate(self, task_class: DataClass) -> None:
        try:
            parsed = UUID(self.reference)
        except (ValueError, AttributeError):
            raise KnowledgeError("invalid_context_reference") from None
        if str(parsed) != self.reference:
            raise KnowledgeError("invalid_context_reference")
        if self.data_class not in allowed_classes(task_class):
            raise KnowledgeError("context_class_forbidden")
        if not self.text or len(self.text.encode("utf-8")) > MAX_DOCUMENT_BYTES + 1024:
            raise KnowledgeError("context_too_large")
        if digest_text(self.text) != self.sha256:
            raise KnowledgeError("context_hash_mismatch")


def validate_contexts(
    blocks: tuple[ContextBlock, ...], *, references: tuple[str, ...], task_class: DataClass
) -> None:
    if len(references) > MAX_CONTEXT_REFS or len(set(references)) != len(references):
        raise KnowledgeError("invalid_context_references")
    if tuple(block.reference for block in blocks) != references:
        raise KnowledgeError("context_set_mismatch")
    for block in blocks:
        block.validate(task_class)
    if sum(len(block.text.encode("utf-8")) for block in blocks) > MAX_CONTEXT_BYTES:
        raise KnowledgeError("context_too_large")
