"""Runtime configuration for the HTTP API."""

from __future__ import annotations

import os


class ApiConfigurationError(RuntimeError):
    """Raised when required API settings are missing."""


def get_worker_token() -> str:
    """Return the shared worker bearer token from ``DECK_WORKER_TOKEN``."""
    token = os.environ.get("DECK_WORKER_TOKEN", "").strip()
    if not token:
        raise ApiConfigurationError("DECK_WORKER_TOKEN is not set")
    return token


def worker_token_configured() -> bool:
    return bool(os.environ.get("DECK_WORKER_TOKEN", "").strip())


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
