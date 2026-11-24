"""Wake payload and the rule that an inbound body is data, not authority."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping

WAKE_VERSION = "deck.wake.v1"
WAKE_EVENT = "task.completed"
STATUS_VERSION = "deck.status.v1"
_MAX_BODY = 1024
_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_HASH = re.compile(r"\A[0-9a-f]{64}\Z")
_STATUS = frozenset({"review", "done"})
_FIELDS = (
    "schema_version",
    "event",
    "task_id",
    "payload_hash",
    "status",
    "correlation_id",
)


class WakeContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require_wake_version(value: str) -> str:
    if value != WAKE_VERSION:
        raise WakeContractError("unsupported_version")
    return value


def wake_body(
    *,
    task_id: uuid.UUID,
    payload_hash: str,
    status: str,
    correlation_id: uuid.UUID,
) -> bytes:
    """Build the only JSON a sender may post. No prompt, result, or secret."""
    require_wake_version(WAKE_VERSION)
    if not _HASH.fullmatch(payload_hash) or status not in _STATUS:
        raise WakeContractError("invalid_wake")
    document = {
        "schema_version": WAKE_VERSION,
        "event": WAKE_EVENT,
        "task_id": str(task_id),
        "payload_hash": payload_hash,
        "status": status,
        "correlation_id": str(correlation_id),
    }
    if tuple(document) != _FIELDS:
        raise WakeContractError("invalid_wake")
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_BODY:
        raise WakeContractError("invalid_wake")
    return encoded


def restrict_untrusted_event(body: bytes) -> dict[str, str]:
    """Keep only wake fields. Drop instructions and every other key.

    The returned mapping is data. Callers must not apply it to a task.
    """
    if not isinstance(body, bytes) or len(body) > _MAX_BODY:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    kept: dict[str, str] = {}
    for key in _FIELDS:
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        if key == "schema_version" and value == WAKE_VERSION:
            kept[key] = value
        elif key == "event" and value == WAKE_EVENT:
            kept[key] = value
        elif key in {"task_id", "correlation_id"} and _UUID.fullmatch(value):
            kept[key] = value
        elif key == "payload_hash" and _HASH.fullmatch(value):
            kept[key] = value
        elif key == "status" and value in _STATUS:
            kept[key] = value
    return kept


def status_document(
    *,
    task_id: uuid.UUID,
    status: str,
    data_class: str,
    payload_hash: str,
    receipt_status: str | None,
    receipt_transport: str | None,
    wake_state: str | None,
    correlation_id: uuid.UUID | None,
    wake_status: str | None,
) -> dict[str, object]:
    """Non-secret poll view. It omits payload, results, prompts, and secrets."""
    receipt: Mapping[str, object] | None
    if receipt_status is None:
        receipt = None
    else:
        receipt = {
            "status": receipt_status,
            "transport": receipt_transport,
            "delivered": receipt_status == "delivered",
        }
    wake: Mapping[str, object] | None
    if wake_state is None:
        wake = None
    else:
        wake = {
            "schema_version": WAKE_VERSION,
            "event": WAKE_EVENT,
            "state": wake_state,
            "status": wake_status,
            "correlation_id": None if correlation_id is None else str(correlation_id),
        }
    return {
        "schema_version": STATUS_VERSION,
        "task_id": str(task_id),
        "status": status,
        "data_class": data_class,
        "payload_hash": payload_hash,
        "receipt": receipt,
        "wake": wake,
    }
