"""Validated HTTP launcher; test callers inject the server function and never bind."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any

from app.core.bind import DeploymentConfigurationError, load_deployment_settings


def _serve(**kwargs: Any) -> None:
    import uvicorn

    uvicorn.run(**kwargs)


def main(
    argv: Sequence[str] | None = None,
    *,
    serve: Callable[..., None] = _serve,
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
