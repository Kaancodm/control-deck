"""Deterministic local simulation; no provider, model download, or network access."""

from __future__ import annotations

import math
from hashlib import sha256

from app.kb.contracts import EMBEDDING_DIMENSION, EMBEDDING_MODEL, KnowledgeError


class FakeLocalEmbeddings:
    """A reproducible test vector, explicitly not semantic model quality."""

    model = EMBEDDING_MODEL
    dimension = EMBEDDING_DIMENSION

    def embed(self, text: str) -> tuple[float, ...]:
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            raise KnowledgeError("invalid_utf8") from None
        if not raw or len(raw) > 24_000:
            raise KnowledgeError("embedding_input_invalid")
        digest = sha256(raw).digest()
        return validate_vector(
            tuple((int.from_bytes(digest[i : i + 4], "big") + 1) / 2**32 for i in range(0, 32, 4)),
            dimension=self.dimension,
        )


def validate_vector(vector: tuple[float, ...], *, dimension: int) -> tuple[float, ...]:
    if len(vector) != dimension or dimension != EMBEDDING_DIMENSION:
        raise KnowledgeError("embedding_dimension_mismatch")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in vector):
        raise KnowledgeError("embedding_invalid")
    if not any(vector):
        raise KnowledgeError("embedding_invalid")
    return vector


def vector_literal(vector: tuple[float, ...]) -> str:
    validated = validate_vector(vector, dimension=EMBEDDING_DIMENSION)
    return "[" + ",".join(str(value) for value in validated) + "]"
