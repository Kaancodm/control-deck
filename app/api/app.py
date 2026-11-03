"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.routes import control, health, knowledge, tasks, worker
from app.api.security import ConfiguredHttpBoundary
from app.db.ui_auth import SessionCapacityError
from app.services.tasks import OwnerTaskError
from app.ui.auth import AuthConfigurationError
from app.ui.files import UiFileError
from app.ui.knowledge import router as knowledge_ui_router
from app.ui.middleware import UiSecurityMiddleware
from app.ui.routes import UiError, ui_error_handler
from app.ui.routes import router as ui_router


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
    app.add_middleware(UiSecurityMiddleware)
    for exception in (
        UiError,
        AuthConfigurationError,
        SessionCapacityError,
        UiFileError,
        OwnerTaskError,
    ):
        app.add_exception_handler(exception, ui_error_handler)
    app.include_router(health.router)
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(worker.router, prefix="/api/v1")
    app.include_router(control.router, prefix="/api/v1")
    app.include_router(knowledge_ui_router)
    app.include_router(ui_router)
    app.mount(
        "/ui/static",
        StaticFiles(directory=Path(__file__).parents[1] / "ui" / "static"),
        name="ui-static",
    )
    app.include_router(knowledge.router, prefix="/api/v1")
    app.add_middleware(ConfiguredHttpBoundary)
    return app


app = create_app()
