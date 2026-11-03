"""Write bounded metadata without subprocess output or filesystem paths."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.operations.models import OperationEvent

_COUNTS = {"tasks", "files", "maps", "snapshots", "backups", "bytes", "rows"}


def record_operation(
    session: Session,
    *,
    operation_id: UUID,
    operation_type: str,
    phase: str,
    task_id: UUID | None = None,
    reason: str | None = None,
    sha256: str | None = None,
    counts: dict[str, int] | None = None,
) -> OperationEvent:
    """Append a phase in the caller's transaction, never inventing a task event."""
    if operation_type not in {"backup", "restore", "retention", "update"} or phase not in {
        "started",
        "artifacts_removed",
        "completed",
        "failed",
    }:
        raise ValueError("Invalid operation phase")
    detail: dict[str, str | int] = {}
    if reason is not None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            raise ValueError("Invalid operation reason")
        detail["reason"] = reason
    if sha256 is not None:
        if re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
            raise ValueError("Invalid operation digest")
        detail["sha256"] = sha256
    for key, value in (counts or {}).items():
        if key not in _COUNTS or type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise ValueError("Invalid operation count")
        detail[key] = value
    event = OperationEvent(
        operation_id=operation_id,
        operation_type=operation_type,
        phase=phase,
        task_id=task_id,
        detail=detail,
    )
    session.add(event)
    session.flush()
    return event
