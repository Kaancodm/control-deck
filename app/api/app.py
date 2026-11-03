"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.routes import control, health, tasks, worker


def create_app() -> FastAPI:
    """Build the Control Deck HTTP API (versioned under ``/api/v1``)."""
    app = FastAPI(
        title="Control Deck API",
        version="0.1.0",
        description=(
            "Schema-validated task orchestration (task.v1 / result.v1) with "
            "PostgreSQL queue and owner approval gate."
        ),
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(worker.router, prefix="/api/v1")
    app.include_router(control.router, prefix="/api/v1")
    return app


app = create_app()
