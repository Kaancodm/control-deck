"""Literal loopback URL checks. Imports stay inside the standard library."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.bind import DeploymentConfigurationError, parse_address

_ENDPOINT = re.compile(r"http://(127\.0\.0\.1|\[::1\]):([0-9]{1,5})\Z")


class LiveConfigurationError(ValueError):
    """Expose a fixed reason without including configuration values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OllamaEndpoint:
    host: str
    port: int

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


def live_enabled(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    return environment.get("DECK_LIVE_ENABLED") in {"1", "true"}


def validate_ollama_url(value: str) -> OllamaEndpoint:
    """Reject resolution, proxy destinations and URL parsing normalizations."""
    match = _ENDPOINT.fullmatch(value)
    if match is None:
        raise LiveConfigurationError("live_url_invalid")
    try:
        address = parse_address(match[1].removeprefix("[").removesuffix("]"))
        port = int(match[2])
        if not address.is_loopback or not 1 <= port <= 65535:
            raise ValueError
    except (DeploymentConfigurationError, ValueError):
        raise LiveConfigurationError("live_url_invalid") from None
    return OllamaEndpoint(str(address), port)
