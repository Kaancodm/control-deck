"""Lane-bound claims, explicit start, committed control and fenced results."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.config import default_heartbeat_extend_seconds, default_lease_ttl_seconds
from app.api.deps import DbSession, WorkerAuth
from app.api.errors import problem_response
from app.api.schemas import (
    LeaseOut,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerLeaseResponse,
    WorkerResultRequest,
    WorkerResultResponse,
    WorkerStartRequest,
    WorkerTaskOut,
)
from app.api.serializers import task_summary
from app.core.schemas import Worker
from app.db.settings import RunnerSettingsError
from app.db.state_machine import InvalidTaskTransitionError
from app.db.transitions import LeaseOwnershipError
from app.db.worker_ops import (
    TaskDeadlineElapsed,
    WorkerOperationError,
    control_attempt,
    start_worker_attempt,
    submit_worker_result,
)
from app.privacy.config import PrivacyConfigurationError
from app.privacy.gate import PrivacyInputError
from app.redaction.artifacts import PayloadArtifactError
from app.redaction.engine import RedactionError
from app.routing.config import RouterConfigurationError
from app.workers.contracts import ControlDecision
from app.workers.preparation import claim_prepared_attempt

router = APIRouter(prefix="/worker", tags=["worker"])


class WorkerLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: Worker
    holder: str | None = Field(default=None, max_length=256)
    lease_ttl_seconds: int | None = Field(default=None, ge=30, le=7200)


def _control_response(decision: ControlDecision) -> WorkerHeartbeatResponse:
    return WorkerHeartbeatResponse(
        action=decision.action,
        expires_at=decision.expires_at,
        heartbeat_at=decision.heartbeat_at,
    )


@router.post("/lease", response_model=None)
def worker_lease(
    body: WorkerLeaseRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerLeaseResponse | JSONResponse:
    """Prepare and commit the claimed attempt before exposing transport bytes."""
    try:
        prepared = claim_prepared_attempt(
            session,
            worker=body.worker,
            lease_ttl=timedelta(seconds=body.lease_ttl_seconds or default_lease_ttl_seconds()),
            runs_root=Path(os.environ.get("DECK_RUNS_DIR", "runs")),
            holder=body.holder,
        )
        # Dependency cleanup may run after response delivery. Commit here so a
        # remote worker can never receive text whose local maps were rolled back.
        session.commit()
    except (RouterConfigurationError, PrivacyConfigurationError, RunnerSettingsError):
        session.rollback()
        return problem_response(
            status=503,
            title="Dispatch configuration unavailable",
            detail="Local routing, privacy and runner configuration must be valid.",
        )
    except (PayloadArtifactError, RedactionError, PrivacyInputError):
        session.rollback()
        return problem_response(
            status=503,
            title="Attempt preparation unavailable",
            detail="The attempt could not be prepared safely; no payload was delivered.",
        )
    if prepared is None:
        return problem_response(
            status=status.HTTP_404_NOT_FOUND,
            title="No work available",
            detail="Queue has no eligible queued tasks for this lane.",
            type_uri="https://control-deck.local/problems/queue-empty",
        )
    return WorkerLeaseResponse(
        task=WorkerTaskOut(
            id=prepared.task_id,
            attempt=prepared.attempt,
            kind=prepared.kind,
            worker=prepared.lane,
            model=prepared.model,
            timeout_s=prepared.timeout_s,
            deadline_at=prepared.deadline_at,
        ),
        lease=LeaseOut(
            lease_id=prepared.lease_id,
            task_id=prepared.task_id,
            expires_at=prepared.lease_expires_at,
            heartbeat_at=prepared.heartbeat_at,
            holder=body.holder,
        ),
        payload_text=prepared.payload_text,
        payload_sha256=prepared.payload_sha256,
        redaction_applied=prepared.redaction_applied,
        redaction_placeholders=prepared.redaction_placeholders,
        artifact_path=prepared.artifact_path,
    )


@router.post("/start", response_model=WorkerHeartbeatResponse)
def worker_start(
    body: WorkerStartRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerHeartbeatResponse:
    """Explicitly start one prepared attempt; return committed stop decisions."""
    try:
        decision = start_worker_attempt(
            session,
            task_id=body.task_id,
            lease_id=body.lease_id,
            attempt=body.attempt,
            worker=body.worker,
        )
        session.commit()
    except WorkerOperationError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _control_response(decision)


@router.post("/heartbeat", response_model=WorkerHeartbeatResponse)
def worker_heartbeat(
    body: WorkerHeartbeatRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerHeartbeatResponse:
    """Commit expiry/control changes before the caller acts on the decision."""
    decision = control_attempt(
        session,
        task_id=body.task_id,
        lease_id=body.lease_id,
        attempt=body.attempt,
        worker=body.worker,
        extend_by=timedelta(seconds=body.extend_seconds or default_heartbeat_extend_seconds()),
    )
    session.commit()
    return _control_response(decision)


@router.post("/result", response_model=None)
def worker_result(
    body: WorkerResultRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerResultResponse | JSONResponse:
    """Validate lane/attempt/preparation identity and accept a started result."""
    try:
        task = submit_worker_result(session, lease_id=body.lease_id, result_payload=body.result)
        response = WorkerResultResponse(task=task_summary(task))
        session.commit()
        return response
    except TaskDeadlineElapsed:
        session.commit()
        return problem_response(status=409, title="Task expired", detail="Owner deadline elapsed.")
    except WorkerOperationError as exc:
        session.rollback()
        return problem_response(
            status=422,
            title="Invalid result",
            detail=str(exc),
            type_uri="https://control-deck.local/problems/invalid-result",
        )
    except LeaseOwnershipError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTaskTransitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
