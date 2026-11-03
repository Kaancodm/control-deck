"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import require_owner
from app.api.errors import problem_response
from app.api.schemas import HealthResponse, ReadyResponse
from app.audit.service import read_lane_health
from app.db.config import DatabaseConfigurationError, get_database_url, get_engine_kwargs
from app.db.session import get_session_factory
from app.db.settings import RunnerSettingsError, read_settings
from app.db.ui_auth import get_ui_session
from app.routing.config import RouterConfigurationError
from app.ui.auth import SESSION_COOKIE, AuthConfigurationError, token_digest

router = APIRouter(tags=["health"])


@router.get("/api/v1/health", response_model=None)
def owner_health(request: Request) -> JSONResponse:
    """Expose local evidence only to the owner; anonymous liveness needs no database."""
    authorization = request.headers.get("authorization")
    token = request.cookies.get(SESSION_COOKIE)
    if authorization is None and token is None and "x-deck-worker-token" not in request.headers:
        return JSONResponse({"status": "ok"})
    if "x-deck-worker-token" in request.headers:
        raise HTTPException(status_code=401, detail="Owner authentication required")
    if authorization is not None:
        require_owner(authorization)
    elif token_digest(token) is None:
        raise HTTPException(status_code=401, detail="Owner authentication required")
    try:
        with get_session_factory()() as session:
            if (
                authorization is None
                and get_ui_session(session, token, touch_activity=False) is None
            ):
                raise HTTPException(status_code=401, detail="Owner authentication required")
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "20251103_0016":
                return JSONResponse(
                    {"status": "unavailable", "db": {"connected": True, "schema": "outdated"}},
                    status_code=503,
                )
            lanes = read_lane_health(session)
            controls = read_settings(session)
            return JSONResponse(
                {
                    "status": "ok",
                    "db": {"connected": True, "schema": "current"},
                    "paused": controls.paused,
                    "stop_requested": controls.stop_requested,
                    "counter_scope": "since_migration_0016",
                    "ollama": next(
                        view.model_dump(mode="json") for view in lanes if view.lane == "ollama"
                    ),
                    "lanes": [view.model_dump(mode="json") for view in lanes],
                }
            )
    except (
        DatabaseConfigurationError,
        SQLAlchemyError,
        RouterConfigurationError,
        RunnerSettingsError,
        AuthConfigurationError,
    ):
        return JSONResponse({"status": "unavailable"}, status_code=503)


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Process is up (no dependency checks)."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=None)
def readyz() -> ReadyResponse | JSONResponse:
    """Verify database connectivity."""
    try:
        engine = create_engine(get_database_url(), **get_engine_kwargs())
    except (DatabaseConfigurationError, SQLAlchemyError):
        return problem_response(
            status=503,
            title="Not ready",
            detail="Database readiness is unavailable.",
            type_uri="https://control-deck.local/problems/not-ready",
        )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return problem_response(
            status=503,
            title="Not ready",
            detail="Database readiness is unavailable.",
            type_uri="https://control-deck.local/problems/not-ready",
        )
    finally:
        engine.dispose()
    return ReadyResponse(status="ok", database="connected")
