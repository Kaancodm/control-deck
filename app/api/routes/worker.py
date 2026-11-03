"""Worker pull, heartbeat, and result submission."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
)
from app.api.serializers import task_detail, task_summary
from app.db.queue import dequeue_next_queued_task
from app.db.state_machine import InvalidTaskTransitionError
from app.db.transitions import LeaseOwnershipError
from app.db.worker_ops import (
    TaskDeadlineElapsed,
    WorkerOperationError,
    refresh_lease_heartbeat,
    submit_worker_result,
)
from app.privacy.config import PrivacyConfigurationError

router = APIRouter(prefix="/worker", tags=["worker"])


class WorkerLeaseRequest(BaseModel):
    holder: str | None = Field(default=None, max_length=256)
    lease_ttl_seconds: int | None = Field(default=None, ge=30, le=7200)


@router.post("/lease", response_model=None)
def worker_lease(
    body: WorkerLeaseRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerLeaseResponse | JSONResponse:
    """Pull the next queued task using ``FOR UPDATE SKIP LOCKED``."""
    ttl_seconds = body.lease_ttl_seconds or default_lease_ttl_seconds()
    try:
        task = dequeue_next_queued_task(
            session,
            lease_ttl=timedelta(seconds=ttl_seconds),
            holder=body.holder,
        )
    except PrivacyConfigurationError:
        return problem_response(
            status=503,
            title="Privacy configuration unavailable",
            detail="The local privacy configuration must be available before dispatch.",
        )
    if task is None:
        return problem_response(
            status=status.HTTP_404_NOT_FOUND,
            title="No work available",
            detail="Queue has no eligible queued tasks.",
            type_uri="https://control-deck.local/problems/queue-empty",
        )
    if task.lease is None:
        raise HTTPException(status_code=500, detail="Lease row missing after dequeue")
    session.refresh(task, attribute_names=["events", "results", "lease"])
    lease = task.lease
    return WorkerLeaseResponse(
        task=task_detail(task),
        lease=LeaseOut(
            lease_id=lease.lease_id,
            task_id=lease.task_id,
            expires_at=lease.expires_at,
            heartbeat_at=lease.heartbeat_at,
            holder=lease.holder,
        ),
    )


@router.post("/heartbeat", response_model=WorkerHeartbeatResponse)
def worker_heartbeat(
    body: WorkerHeartbeatRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerHeartbeatResponse | JSONResponse:
    """Extend an active lease and refresh ``heartbeat_at``."""
    extend = body.extend_seconds or default_heartbeat_extend_seconds()
    try:
        lease = refresh_lease_heartbeat(
            session,
            lease_id=body.lease_id,
            task_id=body.task_id,
            extend_by=timedelta(seconds=extend),
        )
    except TaskDeadlineElapsed as exc:
        return problem_response(status=409, title="Task expired", detail=str(exc))
    except WorkerOperationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkerHeartbeatResponse(
        lease=LeaseOut(
            lease_id=lease.lease_id,
            task_id=lease.task_id,
            expires_at=lease.expires_at,
            heartbeat_at=lease.heartbeat_at,
            holder=lease.holder,
        )
    )


@router.post("/result", response_model=None)
def worker_result(
    body: WorkerResultRequest,
    session: DbSession,
    _worker: WorkerAuth,
) -> WorkerResultResponse | JSONResponse:
    """Submit ``result.v1`` fenced by ``lease_id``."""
    try:
        task = submit_worker_result(
            session,
            lease_id=body.lease_id,
            result_payload=body.result,
        )
    except TaskDeadlineElapsed as exc:
        return problem_response(status=409, title="Task expired", detail=str(exc))
    except WorkerOperationError as exc:
        return problem_response(
            status=422,
            title="Invalid result",
            detail=str(exc),
            type_uri="https://control-deck.local/problems/invalid-result",
        )
    except LeaseOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTaskTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return WorkerResultResponse(task=task_summary(task))
