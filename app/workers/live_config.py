"""Explicit live-lane activation with literal loopback HTTP destinations only.

URL checks live in ``loopback_url`` so the dialogue adapter does not import
Pydantic, the database, or the API stack.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.core.schemas import Worker
from app.workers.loopback_url import (
    LiveConfigurationError,
    OllamaEndpoint,
    live_enabled,
    validate_ollama_url,
)

__all__ = [
    "LiveConfigurationError",
    "OllamaEndpoint",
    "live_enabled",
    "load_live_config",
    "validate_ollama_url",
]


def load_live_config(
    worker: Worker, environment: Mapping[str, str] | None = None
) -> OllamaEndpoint:
    environment = os.environ if environment is None else environment
    if not live_enabled(environment):
        raise LiveConfigurationError("live_disabled")
    if worker != Worker.OLLAMA:
        raise LiveConfigurationError("live_lane_not_supported")
    return validate_ollama_url(environment.get("DECK_OLLAMA_URL", ""))
