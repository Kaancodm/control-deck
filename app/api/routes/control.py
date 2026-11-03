"""Owner-only runner pause, stop and concurrency settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, OwnerAuth
from app.api.errors import problem_response
from app.core.schemas import Worker
from app.db.settings import RunnerSettings, RunnerSettingsError, read_settings, update_settings

router = APIRouter(prefix="/control", tags=["control"])


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any]


class RunnerSettingsOut(BaseModel):
    paused: bool
    stop_requested: bool
    max_parallel: int
    lane_paused: dict[Worker, bool]


def _response(settings: RunnerSettings) -> RunnerSettingsOut:
    return RunnerSettingsOut(
        paused=settings.paused,
        stop_requested=settings.stop_requested,
        max_parallel=settings.max_parallel,
        lane_paused=settings.lane_paused,
    )


@router.get("/settings", response_model=None)
def get_settings(session: DbSession, _owner: OwnerAuth) -> RunnerSettingsOut | JSONResponse:
    try:
        return _response(read_settings(session))
    except RunnerSettingsError:
        return problem_response(
            status=503, title="Runner settings unavailable", detail="Stored settings are invalid."
        )


@router.patch("/settings", response_model=None)
def patch_settings(
    body: SettingsChange, session: DbSession, _owner: OwnerAuth
) -> RunnerSettingsOut | JSONResponse:
    try:
        response = _response(update_settings(session, body.changes))
        session.commit()
        return response
    except RunnerSettingsError:
        session.rollback()
        return problem_response(
            status=422,
            title="Invalid runner settings",
            detail="Use known setting names and values of the required type.",
        )
