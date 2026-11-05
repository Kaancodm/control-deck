"""Prepare an approved attempt locally before its committed transport is exposed."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Lease, Task
from app.db.queue import dequeue_next_queued_task, expire_task_if_due
from app.db.repository import record_gate_error
from app.db.state_machine import TaskState, TaskTransition
from app.db.transitions import LeaseOwnershipError, TransitionActor, transition_task
from app.privacy.gate import FileHash, PrivacyInputError, PrivacyInputUnavailable
from app.redaction.artifacts import PayloadArtifactError, write_payload_sent
from app.redaction.engine import RedactionError
from app.redaction.payload import LaneNotAllowedError, prepare_task_payload
from app.routing.router import RoutingPlan
from app.workers.contracts import PreparedAttempt

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_PREPARATION_ATTEMPTS = 32


class _DeferredPreparation(Exception):
    """Unwind a tentative claim without committing a consumed attempt."""

    def __init__(self, task_id: UUID, source: str) -> None:
        self.task_id = task_id
        self.source = source
        super().__init__("Attachment unavailable during preparation.")


def _approved_file_hashes(session: Session, task: Task) -> tuple[FileHash, ...]:
    """Read only the latest explicit owner's complete file digest set."""
    event = session.scalar(
        select(Event)
        .where(
            Event.task_id == task.id,
            Event.event_type == "task.approve",
            Event.actor == TransitionActor.OPERATOR.value,
        )
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(1)
    )
    if event is None or not isinstance(event.detail, dict):
        raise PrivacyInputError("approval_hashes_unavailable")
    recorded = event.detail.get("file_hashes")
    if not isinstance(recorded, list):
        raise PrivacyInputError("approval_hashes_unavailable")
    hashes: list[FileHash] = []
    seen: set[int] = set()
    for item in recorded:
        if not isinstance(item, dict) or set(item) != {"input_index", "sha256"}:
            raise PrivacyInputError("approval_hashes_unavailable")
        index, digest = item["input_index"], item["sha256"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index in seen
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise PrivacyInputError("approval_hashes_unavailable")
        seen.add(index)
        hashes.append(FileHash(index, digest))
    return tuple(sorted(hashes, key=lambda item: item.input_index))


def _active_lease(session: Session, task: Task) -> Lease:
    """Use the database clock while holding the task before the lease lock."""
    lease = session.scalar(select(Lease).where(Lease.task_id == task.id).with_for_update(of=Lease))
    now = session.scalar(select(func.clock_timestamp()))
    if (
        task.status != TaskState.LEASED
        or task.approved_at is None
        or task.attempt < 1
        or task.cancel_requested
        or lease is None
        or not isinstance(now, datetime)
        or lease.expires_at <= now
        or (task.deadline_at is not None and task.deadline_at <= now)
    ):
        raise PrivacyInputError("attempt_no_longer_active")
    return lease


def prepare_claimed_task(session: Session, task: Task, *, runs_root: Path) -> PreparedAttempt:
    """Prepare a claimed task; the caller must commit before delivering this value.

    Maps and the prepare event share a savepoint. A failed database commit can
    leave an immutable unsent artifact, but never an executable transport claim.
    """
    with session.begin_nested():
        locked = session.scalar(
            select(Task)
            .where(Task.id == task.id)
            .with_for_update(of=Task)
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise PrivacyInputError("attempt_no_longer_active")
        lease = _active_lease(session, locked)
        try:
            model = TaskV1.model_validate(locked.payload)
        except ValidationError:
            raise PrivacyInputError("invalid_task") from None
        try:
            lane = Worker(locked.lane or "")
            plan = RoutingPlan.model_validate(locked.plan)
        except ValueError:
            raise PrivacyInputError("invalid_routing_plan") from None
        if (
            plan.status != "ready"
            or not 0 <= plan.cursor < len(plan.chain)
            or plan.chain[plan.cursor] != lane
        ):
            raise PrivacyInputError("invalid_routing_plan")
        hashes = _approved_file_hashes(session, locked)
        payload = prepare_task_payload(
            session,
            model,
            lane=lane,
            attempt=locked.attempt,
            expected_file_hashes=hashes,
        )
        artifact = write_payload_sent(
            runs_root, task_id=locked.id, attempt=locked.attempt, text=payload.text
        )
        # A slow file read or artifact write must not deliver an expired claim.
        lease = _active_lease(session, locked)
        session.add(
            Event(
                task_id=locked.id,
                event_type="task.prepare",
                actor=TransitionActor.RUNNER.value,
                attempt=locked.attempt,
                lease_id=lease.lease_id,
                from_status=TaskState.LEASED,
                to_status=TaskState.LEASED,
                created_at=func.clock_timestamp(),
                detail={
                    "lane": lane.value,
                    "attempt": locked.attempt,
                    "lease_id": str(lease.lease_id),
                    "payload_sha256": payload.sha256,
                    "redaction_applied": payload.applied,
                    "redaction_placeholders": payload.placeholders,
                    "artifact_path": artifact.relative_path,
                },
            )
        )
        session.flush()
        return PreparedAttempt(
            task_id=locked.id,
            attempt=locked.attempt,
            lease_id=lease.lease_id,
            lane=lane,
            kind=model.kind,
            model=plan.models.get(lane),
            timeout_s=model.policy.timeout_s,
            deadline_at=locked.deadline_at,
            lease_expires_at=lease.expires_at,
            heartbeat_at=lease.heartbeat_at,
            payload_text=payload.text,
            payload_sha256=payload.sha256,
            redaction_applied=payload.applied,
            redaction_placeholders=payload.placeholders,
            artifact_path=artifact.relative_path,
            json_format=any(rule.type == "json_schema" for rule in model.acceptance),
        )


def claim_prepared_attempt(
    session: Session,
    *,
    worker: Worker,
    lease_ttl: timedelta,
    runs_root: Path,
    holder: str | None = None,
    available_models: frozenset[str] | None = None,
) -> PreparedAttempt | None:
    """Claim a safe transport, skipping a bounded number of immutable conflicts.

    The caller owns commit. Privacy mismatches consume a committed claim and
    block it; an attachment that disappears during final preparation rolls back
    the tentative claim and remains queued with a throttled, safe error event.
    Conflicting artifacts block only their task; other artifact failures abort.
    """
    for _ in range(_MAX_PREPARATION_ATTEMPTS):
        try:
            with session.begin_nested():
                task = dequeue_next_queued_task(
                    session,
                    worker=worker,
                    lease_ttl=lease_ttl,
                    holder=holder,
                    available_models=available_models,
                )
                if task is None:
                    return None
                try:
                    return prepare_claimed_task(session, task, runs_root=runs_root)
                except (
                    PrivacyInputError,
                    RedactionError,
                    LaneNotAllowedError,
                    PayloadArtifactError,
                ) as exc:
                    code = getattr(exc, "code", "lane_not_allowed")
                    artifact_conflict = isinstance(exc, PayloadArtifactError)
                    if artifact_conflict and code != "artifact_conflict":
                        raise
                    if code == "file_unavailable":
                        raise _DeferredPreparation(
                            task.id, getattr(exc, "source", "task")
                        ) from None
                    if expire_task_if_due(session, task=task):
                        if artifact_conflict:
                            continue
                        return None
                    lease = session.scalar(select(Lease).where(Lease.task_id == task.id))
                    if lease is None:
                        raise LeaseOwnershipError(
                            "Preparation no longer owns the task lease."
                        ) from None
                    if task.cancel_requested:
                        transition_task(
                            session,
                            task=task,
                            trigger=TaskTransition.CANCEL,
                            lease_id=lease.lease_id,
                            actor=TransitionActor.RUNNER,
                            detail={"reason": "cancel_requested"},
                        )
                    else:
                        transition_task(
                            session,
                            task=task,
                            trigger=TaskTransition.BLOCK,
                            lease_id=lease.lease_id,
                            actor=TransitionActor.RUNNER,
                            detail={
                                "reason": "artifact_conflict"
                                if artifact_conflict
                                else "privacy_gate",
                                "code": code,
                            },
                        )
                    if not artifact_conflict:
                        return None
        except _DeferredPreparation as exc:
            task = session.scalar(
                select(Task).where(Task.id == exc.task_id).with_for_update(of=Task)
            )
            if task is not None:
                record_gate_error(
                    session,
                    task=task,
                    error=PrivacyInputUnavailable("file_unavailable", source=exc.source),
                )
            return None
    return None
