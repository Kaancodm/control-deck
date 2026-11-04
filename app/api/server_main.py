"""Validated HTTP launcher; test callers inject the server function and never bind."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.bind import DeploymentConfigurationError, load_deployment_settings
from app.db.config import DatabaseConfigurationError
from app.db.session import session_scope
from app.db.ui_auth import observe_owner_login_state_on_startup


def _serve(**kwargs: Any) -> None:
    import uvicorn

    uvicorn.run(**kwargs)


def _observe_login_state() -> None:
    """Observe persisted login protection without resetting it before serving."""
    with session_scope() as session:
        observe_owner_login_state_on_startup(session)


def main(
    argv: Sequence[str] | None = None,
    *,
    serve: Callable[..., None] = _serve,
    startup: Callable[[], None] = _observe_login_state,
) -> int:
    parser = argparse.ArgumentParser(description="Validate and serve the Control Deck HTTP app.")
    parser.add_argument(
        "--check-only", action="store_true", help="Validate without opening a socket."
    )
    args = parser.parse_args(argv)
    try:
        settings = load_deployment_settings()
    except DeploymentConfigurationError as exc:
        print(f"HTTP startup rejected: {exc.code}", file=sys.stderr)
        return 2
    if args.check_only:
        print("HTTP deployment configuration is valid; no listener was started.")
        return 0
    try:
        startup()
    except (DatabaseConfigurationError, SQLAlchemyError):
        # Fail closed if persisted login protection cannot be read.
        print("HTTP startup rejected: login_state_unavailable", file=sys.stderr)
        return 2
    serve(
        app="app.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        forwarded_allow_ips="",
        access_log=False,
        server_header=False,
        workers=1,
        ws="none",
        http="h11",
        timeout_keep_alive=5,
        limit_concurrency=32,
        backlog=64,
        h11_max_incomplete_event_size=16384,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
