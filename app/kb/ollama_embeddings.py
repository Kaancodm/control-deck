"""Loopback Ollama embeddings for the knowledge store; never a fallback to the fake.

Before the first ``/api/embed`` call (cached per adapter instance and re-checked on
model change), the adapter queries ``/api/show`` for ``<model>:local`` over the
bounded loopback client and rejects ``remote_host``, ``remote_model``, or any cloud
marker with ``embedding_model_remote``. One ``/api/embed`` request then embeds a
document (all chunks) or query. The response must echo the model, carry no remote
fields, and return one finite, non-zero vector of the contract width per input.
Logs carry counts, width, timing and reason codes only: never input text, vector
values, the URL, ``/api/show`` bodies, or server error text.
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
from app.workers.lanes.ollama import (
    has_cloud_marker,
    is_remote_model_info,
    show_local_model,
)
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
    if exc.status_code == 400:
        # With truncate=false Ollama answers 400 for input beyond the context length. The
        # server text is never read, so this is by status only (NaN/Inf output is also 400).
        return "embedding_input_too_long"
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
        self._verified_model: str | None = None

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
        prefix = query_instruction(self._spec)
        return self._embed((prefix + checked,), purpose="query")[0]

    def _ensure_local_model(self, *, timeout_s: float) -> str:
        model = self._spec.model
        local_model = f"{model}:local"
        if self._verified_model == model:
            return local_model
        self._verified_model = None
        if not model or has_cloud_marker(model) or model.casefold().endswith(":local"):
            raise KnowledgeError("embedding_model_remote")
        info = show_local_model(self._client, model, timeout_s=timeout_s)
        if "error" in info:
            raise KnowledgeError("embedding_malformed")
        if is_remote_model_info(info):
            raise KnowledgeError("embedding_model_remote")
        self._verified_model = model
        return local_model

    def _embed(self, texts: tuple[str, ...], *, purpose: Purpose) -> tuple[tuple[float, ...], ...]:
        detail: dict[str, object] = {
            "purpose": purpose,
            "count": len(texts),
            "dimension": self._spec.dimension,
        }
        started = time.monotonic()
        deadline = started + self._timeout_s
        in_show = True
        try:
            local_model = self._ensure_local_model(timeout_s=_remaining_budget(deadline))
            in_show = False
            response = self._client.embed(
                model=local_model,
                inputs=texts,
                timeout_s=_remaining_budget(deadline),
                keep_alive=self._keep_alive.wire,
            )
            vectors = self._validate(response, len(texts))
        except OllamaHTTPError as exc:
            code = (
                "embedding_unavailable" if in_show and exc.status_code == 400 else failure_code(exc)
            )
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
            or is_remote_model_info(response)
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


def _remaining_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OllamaHTTPError("timeout", phase="connect")
    return remaining


def _elapsed_ms(started: float) -> int:
    return max(0, min(int((time.monotonic() - started) * 1000), 600_000))
