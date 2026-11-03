"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import problem_response
from app.api.schemas import HealthResponse, ReadyResponse
from app.db.config import DatabaseConfigurationError, get_database_url, get_engine_kwargs

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Process is up (no dependency checks)."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=None)
def readyz() -> ReadyResponse | JSONResponse:
    """Verify database connectivity."""
    try:
        engine = create_engine(get_database_url(), **get_engine_kwargs())
    except DatabaseConfigurationError:
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
