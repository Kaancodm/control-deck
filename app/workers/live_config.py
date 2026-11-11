"""Explicit live-lane activation.

Ollama accepts only literal loopback HTTP destinations. Those checks live in
``loopback_url`` so the dialogue adapter does not import Pydantic, the database,
or the API stack. Claude live settings are a separate check and do not start a runner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.schemas import Worker
from app.workers.loopback_url import (
    LiveConfigurationError,
    OllamaEndpoint,
    live_enabled,
    validate_ollama_url,
)

__all__ = [
    "ClaudeLiveConfig",
    "LiveConfigurationError",
    "OllamaEndpoint",
    "live_enabled",
    "load_claude_live_config",
    "load_live_config",
    "validate_ollama_url",
]

# Name plus lowercase sha256 digest. The simulated placeholder image cannot match.
_CLAUDE_IMAGE = re.compile(r"deck-lane-claude@sha256:[0-9a-f]{64}\Z")
# Absolute unix socket: no host, query, empty segment, "." or "..".
_DOCKER_UNIX_HOST = re.compile(r"unix://(?:/(?!\.{1,2}(?:/|\Z))[A-Za-z0-9._-]{1,200})+\Z")
_DOCKER_HOST_MAX_LENGTH = 200
_EXACT_FLAG = frozenset({"1", "true"})


@dataclass(frozen=True)
class ClaudeLiveConfig:
    """Pinned image and socket accepted by the Claude live check."""

    image: str
    docker_host: str


def load_live_config(
    worker: Worker, environment: Mapping[str, str] | None = None
) -> OllamaEndpoint:
    environment = os.environ if environment is None else environment
    if not live_enabled(environment):
        raise LiveConfigurationError("live_disabled")
    if worker != Worker.OLLAMA:
        raise LiveConfigurationError("live_lane_not_supported")
    return validate_ollama_url(environment.get("DECK_OLLAMA_URL", ""))


def load_claude_live_config(
    environment: Mapping[str, str] | None = None,
) -> ClaudeLiveConfig:
    """Require both live flags, the pinned image and an absolute unix socket.

    ``load_live_config`` stays unchanged and still rejects this lane. Nothing here
    starts a process. Reasons are fixed codes and never include the rejected value.
    """
    environment = os.environ if environment is None else environment
    if not live_enabled(environment):
        raise LiveConfigurationError("live_disabled")
    if environment.get("DECK_CLAUDE_LIVE_ENABLED") not in _EXACT_FLAG:
        raise LiveConfigurationError("claude_live_disabled")
    image = environment.get("DECK_CLAUDE_IMAGE", "")
    if _CLAUDE_IMAGE.fullmatch(image) is None:
        raise LiveConfigurationError("claude_image_invalid")
    docker_host = environment.get("DECK_DOCKER_HOST", "")
    if not _absolute_unix_docker_host(docker_host):
        raise LiveConfigurationError("docker_host_invalid")
    return ClaudeLiveConfig(image=image, docker_host=docker_host)


def _absolute_unix_docker_host(value: str) -> bool:
    if len(value) > _DOCKER_HOST_MAX_LENGTH:
        return False
    return _DOCKER_UNIX_HOST.fullmatch(value) is not None
