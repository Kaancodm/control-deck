"""Bounded execution metadata: no arbitrary provider text reaches logs or events."""

from __future__ import annotations

import re
from collections.abc import Mapping

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def safe_execution_metadata(metadata: object) -> dict[str, object]:
    """Copy only validated metadata from trusted adapters, without reflecting failures."""
    if not isinstance(metadata, Mapping):
        return {}
    result: dict[str, object] = {}
    for key, value in metadata.items():
        if key == "transport" and value in ("ollama_http", "claude_cli", "codex_cli"):
            result[key] = value
        elif key == "done_reason" and value in ("stop", "length"):
            result[key] = value
        elif key == "model" and isinstance(value, str) and _MODEL.fullmatch(value):
            result[key] = value
        elif key in {"adapter_version", "ollama_version"} and isinstance(value, str):
            if len(value) <= 32 and _VERSION.fullmatch(value):
                result[key] = value
        elif key == "output_sha256" and isinstance(value, str) and _DIGEST.fullmatch(value):
            result[key] = value
        elif key in {"prompt_eval_count", "eval_count", "duration_ms"}:
            if type(value) is int and 0 <= value <= 2**63 - 1:
                result[key] = value
        elif key == "http_status" and type(value) is int and 100 <= value <= 599:
            result[key] = value
    return result
