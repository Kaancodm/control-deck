"""Parse Ollama ``keep_alive`` values for loopback chat requests."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Matches assistant dialogue defaults; lane uses the same bounded duration grammar.
_DURATION = re.compile(
    r"0|-1m|[1-9][0-9]{0,3}[sm]|[1-9]h|1[0-9]h|2[0-4]h",
    re.ASCII,
)
KEEP_ALIVE_SERVER = "server"


@dataclass(frozen=True)
class KeepAliveSetting:
    """Configured keep-alive: ``wire`` is sent in JSON unless ``omit`` is true."""

    configured: str
    wire: str | int | None

    @property
    def omit(self) -> bool:
        return self.wire is None


def parse_keep_alive_value(raw: str) -> KeepAliveSetting:
    """Validate one non-empty keep-alive token.

    Accepts bounded duration strings (including ``-1m`` for keep-forever per
    Ollama 0.40.x), JSON number ``-1`` (normalised from env ``-1``), and
    ``server`` to omit ``keep_alive`` so the daemon default applies. The bare
    string ``-1`` is not a valid Ollama duration and is mapped to integer ``-1``.
    """
    if not isinstance(raw, str) or raw != raw.strip():
        raise ValueError("Invalid keep_alive")
    value = raw.strip()
    folded = value.casefold()
    if folded == KEEP_ALIVE_SERVER:
        return KeepAliveSetting(configured=KEEP_ALIVE_SERVER, wire=None)
    if value == "-1":
        return KeepAliveSetting(configured="-1", wire=-1)
    if _DURATION.fullmatch(value) is None:
        raise ValueError("Invalid keep_alive")
    return KeepAliveSetting(configured=value, wire=value)


def lane_keep_alive_from_env(
    environment: dict[str, str] | None = None,
    *,
    default: str = "1m",
) -> KeepAliveSetting:
    """Read ``DECK_OLLAMA_KEEP_ALIVE`` with a safe fallback on invalid input."""
    env = os.environ if environment is None else environment
    raw = env.get("DECK_OLLAMA_KEEP_ALIVE", "").strip()
    if not raw:
        return parse_keep_alive_value(default)
    try:
        return parse_keep_alive_value(raw)
    except ValueError:
        return parse_keep_alive_value(default)
