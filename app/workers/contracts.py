"""Safe host-to-lane contracts without original tasks or local redaction maps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from app.core.schemas import ResultV1, TaskKind, Worker

ExecutionProfile = Literal["simulation", "live", "remote", "unknown"]
# Profiles an adapter may declare for itself. ``remote`` and ``unknown`` are assigned by
# the host (HTTP worker API, legacy results) and can never be claimed by an executor.
DeclaredProfile = Literal["disabled", "simulation", "live"]


class ControlAction(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    EXPIRED = "expired"
    LOST_LEASE = "lost_lease"


@dataclass(frozen=True)
class ControlDecision:
    action: ControlAction
    expires_at: datetime | None = None
    heartbeat_at: datetime | None = None


@dataclass(frozen=True)
class PreparedAttempt:
    """A prepared transport envelope; it is safe only after its transaction commits."""

    task_id: UUID
    attempt: int
    lease_id: UUID
    lane: Worker
    kind: TaskKind
    model: str | None
    timeout_s: int
    deadline_at: datetime | None
    lease_expires_at: datetime
    heartbeat_at: datetime
    payload_text: str = field(repr=False)
    payload_sha256: str
    redaction_applied: bool
    redaction_placeholders: int
    artifact_path: str
    json_format: bool = False


@dataclass(frozen=True)
class AttemptContext:
    """Only prepared text and execution metadata cross into a lane adapter."""

    task_id: UUID
    attempt: int
    lease_id: UUID
    lane: Worker
    kind: TaskKind
    model: str | None
    timeout_s: int
    deadline_at: datetime | None
    run_dir: Path
    payload_text: str = field(repr=False)
    payload_sha256: str
    redaction_applied: bool
    redaction_placeholders: int
    cancel_requested: Callable[[], bool] = field(repr=False)
    json_format: bool = False


class LaneExecutor(Protocol):
    lane: Worker
    # Bounded metadata of the most recent ``execute`` call. The runner clears it before
    # every attempt and copies only allow-listed keys via ``safe_execution_metadata``.
    last_metadata: dict[str, object]

    @property
    def profile(self) -> DeclaredProfile:
        """Declare provenance independently of runner activation flags."""
        ...

    def execute(self, context: AttemptContext) -> ResultV1:
        """Execute once, honour cancellation, and return a validated lane result."""
        ...
