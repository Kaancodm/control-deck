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
from app.db.ui_auth import release_owner_login_lock_on_startup


def _serve(**kwargs: Any) -> None:
    import uvicorn

    uvicorn.run(**kwargs)


def _release_login_lock() -> None:
    """A service restart lifts the global login lock before the listener opens (D3)."""
    with session_scope() as session:
        release_owner_login_lock_on_startup(session)


def main(
    argv: Sequence[str] | None = None,
    *,
    serve: Callable[..., None] = _serve,
    startup: Callable[[], None] = _release_login_lock,
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
        # Fail closed: a start that cannot honour "locked until restart" must not serve logins.
        print("HTTP startup rejected: login_lock_release_failed", file=sys.stderr)
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
