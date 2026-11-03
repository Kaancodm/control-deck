"""Runtime configuration for the HTTP API."""

from __future__ import annotations

import os

from app.core.schemas import Worker


class ApiConfigurationError(RuntimeError):
    """Raised when required API settings are missing."""


def get_worker_tokens(*, require_configured: bool = True) -> dict[Worker, str]:
    """Return unambiguous lane credentials without a shared-token fallback."""
    tokens = {
        worker: token
        for worker in Worker
        if (token := os.environ.get(f"DECK_WORKER_TOKEN_{worker.value.upper()}", "").strip())
    }
    owner = os.environ.get("DECK_OWNER_TOKEN", "").strip()
    if (
        (require_configured and not tokens)
        or len(set(tokens.values())) != len(tokens)
        or owner in tokens.values()
    ):
        raise ApiConfigurationError("Worker lane authentication is unavailable or ambiguous")
    return tokens


def get_owner_token() -> str:
    """Return the shared owner bearer token from ``DECK_OWNER_TOKEN``."""
    token = os.environ.get("DECK_OWNER_TOKEN", "").strip()
    if not token:
        raise ApiConfigurationError("DECK_OWNER_TOKEN is not set")
    return token


def owner_token_configured() -> bool:
    return bool(os.environ.get("DECK_OWNER_TOKEN", "").strip())


def default_lease_ttl_seconds() -> int:
    raw = os.environ.get("DECK_LEASE_TTL_SECONDS", "900").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiConfigurationError("DECK_LEASE_TTL_SECONDS must be an integer") from exc
    if value < 30:
        raise ApiConfigurationError("DECK_LEASE_TTL_SECONDS must be at least 30")
    return value


def default_heartbeat_extend_seconds() -> int:
    raw = os.environ.get("DECK_HEARTBEAT_EXTEND_SECONDS", "300").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiConfigurationError("DECK_HEARTBEAT_EXTEND_SECONDS must be an integer") from exc
    if value < 30:
        raise ApiConfigurationError("DECK_HEARTBEAT_EXTEND_SECONDS must be at least 30")
    return value
