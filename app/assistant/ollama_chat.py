"""Opt-in dialogue over the existing Ollama loopback client.

The adapter speaks ``/api/chat`` only after ``DECK_ASSISTANT_OLLAMA=1`` and a
validated loopback URL plus an explicit ``name:tag`` model. It never pulls a
model, never reads the runner live flag, and never records an uncensored or
installed-model attestation.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping

from app.assistant.errors import AssistantError
from app.assistant.providers import ModelReply
from app.assistant.text import MESSAGE_MAX, reject_secrets, require_data_class
from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from app.workers.loopback_url import LiveConfigurationError, validate_ollama_url

LOOPBACK_PREFIX = "Loopback, Identität nicht belegt."
# One already-installed CPU model can spend the first several seconds loading.
# The previous dialogue cap was 8s and collapsed every failure into one code.
# These bounds stay inside a 90s operator shell. There is no second attempt.
DIALOGUE_TIMEOUT_S = 75.0
DIALOGUE_FIRST_CHUNK_S = 60.0
DIALOGUE_IDLE_S = 20.0
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_PHASES = {"connect", "headers", "body", "status", "unknown"}
_REASONS = {"cancelled", "timeout", "unavailable", "internal"}


def validate_model_tag(model: str) -> str:
    """Require one explicit tag. Reject cloud aliases and the runner's ``:local`` suffix."""
    if (
        not isinstance(model, str)
        or not _MODEL.fullmatch(model)
        or model.count(":") != 1
        or not all(model.split(":"))
        or ":cloud" in model.casefold()
        or model.casefold().endswith("-cloud")
        or model.casefold().endswith(":local")
    ):
        raise AssistantError("assistant_model_unconfigured")
    return model


class OllamaDialogueAdapter:
    """One loopback chat call. ``model_identity`` stays ``unverified``."""

    profile = "ollama_loopback"

    def __init__(self, *, url: str, model: str, client: OllamaHTTPClient | None = None) -> None:
        self.url = url
        self.model = validate_model_tag(model)
        try:
            validate_ollama_url(url)
        except LiveConfigurationError as exc:
            raise AssistantError("assistant_model_unconfigured") from exc
        self.client = client or OllamaHTTPClient(
            url,
            first_chunk_timeout_s=DIALOGUE_FIRST_CHUNK_S,
            idle_timeout_s=DIALOGUE_IDLE_S,
        )

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> OllamaDialogueAdapter:
        env = os.environ if environment is None else environment
        if env.get("DECK_ASSISTANT_OLLAMA") != "1":
            raise AssistantError("assistant_model_unconfigured")
        url = env.get("DECK_OLLAMA_URL", "").strip()
        model = env.get("DECK_ASSISTANT_OLLAMA_MODEL", "").strip()
        if not url or not model:
            raise AssistantError("assistant_model_unconfigured")
        return cls(url=url, model=model)

    def complete(
        self,
        prompt: str,
        *,
        data_class: str,
        cancelled: Callable[[], bool] | None = None,
        timeout_s: float = DIALOGUE_TIMEOUT_S,
    ) -> ModelReply:
        checked = require_data_class(data_class)
        reject_secrets(prompt, source="dialogue_prompt")
        if len(prompt) > MESSAGE_MAX:
            raise AssistantError("assistant_oversize")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise AssistantError("assistant_model_unconfigured")
        if not 0 < float(timeout_s) <= DIALOGUE_TIMEOUT_S:
            raise AssistantError("assistant_model_unconfigured")
        stop = cancelled if cancelled is not None else (lambda: False)
        started = time.monotonic()
        try:
            response = self.client.chat(
                model=f"{self.model}:local",
                payload_text=prompt,
                timeout_s=float(timeout_s),
                cancelled=stop,
                num_ctx=1024,
                keep_alive="0",
            )
        except OllamaHTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            raise AssistantError(
                _failure_code(exc), detail=_failure_detail(exc, elapsed_ms)
            ) from exc
        content = response.content.strip()
        if not content or "\0" in content:
            raise AssistantError(
                "assistant_model_malformed",
                detail={"class": "malformed", "phase": "body", "reason": "internal"},
            )
        reject_secrets(content, source="dialogue_reply")
        text = f"{LOOPBACK_PREFIX} {content[:2000]}"
        if len(text) > MESSAGE_MAX:
            text = text[:MESSAGE_MAX]
        return ModelReply(
            text=text,
            profile=self.profile,
            data_class=checked,
            profile_written=False,
            live=False,
            network_calls=0,
            uncensored_claim=False,
            loopback_calls=1,
            model_identity="unverified",
        )


def _failure_code(exc: OllamaHTTPError) -> str:
    if exc.reason == "cancelled":
        return "assistant_cancelled"
    if exc.status_code == 404:
        return "assistant_model_missing"
    if exc.status_code is not None:
        return "assistant_model_http"
    if exc.reason == "timeout" and exc.phase == "connect":
        return "assistant_model_connect_timeout"
    if exc.reason == "timeout":
        return "assistant_model_read_timeout"
    if exc.reason == "unavailable" and exc.phase == "connect":
        return "assistant_model_connect_failed"
    if exc.reason == "internal":
        return "assistant_model_malformed"
    return "assistant_model_unavailable"


_FAILURE_CLASS = {
    "assistant_cancelled": "cancelled",
    "assistant_model_missing": "model_missing",
    "assistant_model_http": "http_status",
    "assistant_model_connect_timeout": "connect_timeout",
    "assistant_model_read_timeout": "read_timeout",
    "assistant_model_connect_failed": "connect_failed",
    "assistant_model_malformed": "malformed",
    "assistant_model_unavailable": "unavailable",
}


def _failure_detail(exc: OllamaHTTPError, elapsed_ms: int) -> dict[str, object]:
    code = _failure_code(exc)
    detail: dict[str, object] = {
        "class": _FAILURE_CLASS[code],
        "phase": exc.phase if exc.phase in _PHASES else "unknown",
        "reason": exc.reason if exc.reason in _REASONS else "internal",
        "elapsed_ms": max(0, min(elapsed_ms, 120_000)),
    }
    if type(exc.status_code) is int and 100 <= exc.status_code <= 599:
        detail["http_status"] = exc.status_code
    return detail
