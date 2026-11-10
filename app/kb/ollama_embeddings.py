"""Loopback Ollama embeddings for the knowledge store; never a fallback to the fake.

One ``/api/embed`` request per document (all chunks) or query. The response must
echo the model, carry no remote fields, and return one finite, non-zero vector of
the contract width per input. Logs carry counts, width, timing and reason codes
only: never input text, vector values, the URL, or server error text.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Literal

from app.kb.contracts import EmbeddingSpec, KnowledgeError
from app.kb.embeddings import check_inputs, validate_vector
from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from app.workers.lanes.ollama_keep_alive import KeepAliveSetting

_log = logging.getLogger(__name__)

Purpose = Literal["document", "query"]

# Qwen3-Embedding is asymmetric: queries carry a task instruction, documents stay bare.
# Bound to the exact model tag so another model never inherits it. Owner A/B, 2026-11-10.
QWEN3_QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)
QUERY_INSTRUCTIONS: Mapping[str, str] = MappingProxyType(
    {"qwen3-embedding:0.6b": QWEN3_QUERY_INSTRUCTION}
)


def query_instruction(spec: EmbeddingSpec) -> str:
    """The fixed query prefix for this model tag, or an empty string."""
    return QUERY_INSTRUCTIONS.get(spec.model, "")


def failure_code(exc: OllamaHTTPError) -> str:
    """Map a transport category to a stable knowledge reason code."""
    if exc.reason == "busy":
        return "embedding_busy"
    if exc.reason == "timeout":
        return "embedding_timeout"
    if exc.reason == "internal":
        return "embedding_malformed"
    if exc.status_code == 404:
        return "embedding_model_missing"
    return "embedding_unavailable"


def _log_call(detail: Mapping[str, object]) -> None:
    payload = {"event": "kb_embedding_call", **dict(detail)}
    _log.info("%s", json.dumps(payload, sort_keys=True))


class OllamaEmbeddings:
    """Embed through the existing loopback client; the spec is fixed at construction."""

    def __init__(
        self,
        *,
        client: OllamaHTTPClient,
        spec: EmbeddingSpec,
        timeout_s: float,
        keep_alive: KeepAliveSetting,
    ) -> None:
        if spec.provider != "ollama":
            raise KnowledgeError("knowledge_embedding_configuration_invalid")
        self._client = client
        self._spec = spec
        self._timeout_s = timeout_s
        self._keep_alive = keep_alive
        self._query_prefix = query_instruction(spec)

    def __repr__(self) -> str:
        return f"OllamaEmbeddings(model={self._spec.model!r}, dimension={self._spec.dimension})"

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._embed(check_inputs(texts), purpose="document")

    def embed_query(self, text: str) -> tuple[float, ...]:
        # Bound the caller's text first; the fixed prefix is not counted against it.
        (checked,) = check_inputs((text,))
        return self._embed((self._query_prefix + checked,), purpose="query")[0]

    def _embed(self, texts: tuple[str, ...], *, purpose: Purpose) -> tuple[tuple[float, ...], ...]:
        detail: dict[str, object] = {
            "purpose": purpose,
            "count": len(texts),
            "dimension": self._spec.dimension,
        }
        started = time.monotonic()
        try:
            response = self._client.embed(
                model=f"{self._spec.model}:local",
                inputs=texts,
                timeout_s=self._timeout_s,
                keep_alive=self._keep_alive.wire,
            )
            vectors = self._validate(response, len(texts))
        except OllamaHTTPError as exc:
            code = failure_code(exc)
            _log_call(
                detail
                | {
                    "elapsed_ms": _elapsed_ms(started),
                    "status": code,
                    "phase": exc.phase,
                    "reason": exc.reason,
                    "http_status": exc.status_code,
                }
            )
            raise KnowledgeError(code) from None
        except KnowledgeError as exc:
            _log_call(
                detail
                | {
                    "elapsed_ms": _elapsed_ms(started),
                    "status": exc.code,
                    "phase": "validation",
                    "reason": "response",
                    "http_status": 200,
                }
            )
            raise
        _log_call(
            detail
            | {
                "elapsed_ms": _elapsed_ms(started),
                "status": "ok",
                "phase": "done",
                "reason": None,
                "http_status": 200,
            }
        )
        return vectors

    def _validate(
        self, response: Mapping[str, object], expected: int
    ) -> tuple[tuple[float, ...], ...]:
        model = response.get("model")
        if (
            model not in {self._spec.model, f"{self._spec.model}:local"}
            or "error" in response
            or response.get("remote_host")
            or response.get("remote_model")
        ):
            raise KnowledgeError("embedding_malformed")
        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            raise KnowledgeError("embedding_malformed")
        vectors: list[tuple[float, ...]] = []
        for item in embeddings:
            if not isinstance(item, list):
                raise KnowledgeError("embedding_malformed")
            vectors.append(validate_vector(item, dimension=self._spec.dimension))
        return tuple(vectors)


def _elapsed_ms(started: float) -> int:
    return max(0, min(int((time.monotonic() - started) * 1000), 600_000))
