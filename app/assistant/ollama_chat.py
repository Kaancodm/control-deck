"""Opt-in dialogue over the existing Ollama loopback client.

The adapter speaks ``/api/chat`` only after ``DECK_ASSISTANT_OLLAMA=1`` and a
validated loopback URL plus an explicit ``name:tag`` model. It never pulls a
model, never reads the runner live flag, and never records an uncensored or
installed-model attestation.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping

from app.assistant.errors import AssistantError
from app.assistant.providers import ModelReply
from app.assistant.text import MESSAGE_MAX, reject_secrets, require_data_class
from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from app.workers.loopback_url import LiveConfigurationError, validate_ollama_url

LOOPBACK_PREFIX = "Loopback, Identität nicht belegt."
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


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
        self.client = client or OllamaHTTPClient(url, first_chunk_timeout_s=8, idle_timeout_s=8)

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
    ) -> ModelReply:
        checked = require_data_class(data_class)
        reject_secrets(prompt, source="dialogue_prompt")
        if len(prompt) > MESSAGE_MAX:
            raise AssistantError("assistant_oversize")
        stop = cancelled if cancelled is not None else (lambda: False)
        try:
            response = self.client.chat(
                model=f"{self.model}:local",
                payload_text=prompt,
                timeout_s=8,
                cancelled=stop,
                num_ctx=1024,
                keep_alive="0",
            )
        except OllamaHTTPError as exc:
            raise AssistantError("assistant_model_unavailable") from exc
        content = response.content.strip()
        if not content or "\0" in content:
            raise AssistantError("assistant_model_unavailable")
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
