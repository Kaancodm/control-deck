"""Embedding provider protocol, vector checks, and the deterministic local simulation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from hashlib import sha256
from typing import Protocol

from app.kb.contracts import (
    FAKE_EMBEDDING_SPEC,
    MAX_DOCUMENT_BYTES,
    EmbeddingSpec,
    KnowledgeError,
)

# put_document splits at most MAX_DOCUMENT_BYTES into 1500-character chunks.
MAX_EMBEDDING_BATCH = 16


class EmbeddingProvider(Protocol):
    """Documents and queries are separate calls so a query instruction can differ later."""

    @property
    def spec(self) -> EmbeddingSpec: ...

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


def check_inputs(texts: Sequence[str]) -> tuple[str, ...]:
    """Bound a batch before any provider sees it; errors never contain the text."""
    if isinstance(texts, str) or not 1 <= len(texts) <= MAX_EMBEDDING_BATCH:
        raise KnowledgeError("embedding_input_invalid")
    checked: list[str] = []
    for item in texts:
        if not isinstance(item, str):
            raise KnowledgeError("embedding_input_invalid")
        try:
            raw = item.encode("utf-8")
        except UnicodeError:
            raise KnowledgeError("invalid_utf8") from None
        if not raw or len(raw) > MAX_DOCUMENT_BYTES:
            raise KnowledgeError("embedding_input_invalid")
        checked.append(item)
    return tuple(checked)


class FakeLocalEmbeddings:
    """A reproducible test vector, explicitly not semantic model quality."""

    spec = FAKE_EMBEDDING_SPEC

    def _vector(self, text: str) -> tuple[float, ...]:
        raw = text.encode("utf-8")
        values: list[float] = []
        counter = 0
        while len(values) < self.spec.dimension:
            digest = sha256(counter.to_bytes(4, "big") + raw).digest()
            values.extend(
                (int.from_bytes(digest[i : i + 4], "big") + 1) / 2**32 for i in range(0, 32, 4)
            )
            counter += 1
        return validate_vector(tuple(values[: self.spec.dimension]), dimension=self.spec.dimension)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(item) for item in check_inputs(texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(check_inputs((text,))[0])


def validate_vector(vector: Sequence[object], *, dimension: int) -> tuple[float, ...]:
    if isinstance(vector, (str, bytes)) or len(vector) != dimension:
        raise KnowledgeError("embedding_dimension_mismatch")
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise KnowledgeError("embedding_invalid")
        try:
            number = float(value)
        except OverflowError:
            raise KnowledgeError("embedding_invalid") from None
        if not math.isfinite(number):
            raise KnowledgeError("embedding_invalid")
        values.append(number)
    if not any(values):
        raise KnowledgeError("embedding_invalid")
    return tuple(values)


def vector_literal(vector: Sequence[object], *, dimension: int) -> str:
    validated = validate_vector(vector, dimension=dimension)
    return "[" + ",".join(repr(value) for value in validated) + "]"
