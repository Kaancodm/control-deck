"""FastAPI dependencies (database session, auth placeholders)."""

from __future__ import annotations

import hmac
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.config import (
    ApiConfigurationError,
    get_owner_token,
    get_worker_tokens,
    owner_token_configured,
)
from app.core.schemas import Worker
from app.db.session import get_session_factory


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session (commit on success, rollback on error)."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def require_owner(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    Validate owner access via ``Authorization: Bearer`` (``DECK_OWNER_TOKEN``).

    Replaced by login sessions in Etappe 15.
    """
    if not owner_token_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Owner authentication is not configured (DECK_OWNER_TOKEN)",
        )
    # Owner-only setup is valid, but ambiguous lane tokens must not turn a
    # worker credential into owner access through a configuration collision.
    try:
        get_worker_tokens(require_configured=False)
    except ApiConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication configuration is unavailable or ambiguous",
        ) from exc
    expected = get_owner_token()
    provided = _extract_bearer(authorization)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing owner token",
        )


OwnerAuth = Annotated[None, Depends(require_owner)]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def require_worker_token(
    authorization: Annotated[str | None, Header()] = None,
    x_deck_worker_token: Annotated[str | None, Header(alias="X-Deck-Worker-Token")] = None,
) -> Worker:
    """Derive one authenticated lane from its dedicated configured credential."""
    try:
        tokens = get_worker_tokens()
    except ApiConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker lane authentication is unavailable or ambiguous",
        ) from exc
    bearer = _extract_bearer(authorization)
    header_token = (x_deck_worker_token or "").strip() or None
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing worker token",
    )
    # Neither a malformed bearer nor conflicting credentials may fall back to
    # another header: the request must identify exactly one authenticated lane.
    if authorization is not None and bearer is None:
        raise invalid
    if bearer is not None and x_deck_worker_token is not None:
        if header_token is None or not hmac.compare_digest(bearer.encode(), header_token.encode()):
            raise invalid
    provided = bearer or header_token
    if not provided:
        raise invalid
    matched = None
    for worker, expected in tokens.items():
        if hmac.compare_digest(provided.encode(), expected.encode()):
            matched = worker
    if matched is None:
        raise invalid
    return matched


WorkerAuth = Annotated[Worker, Depends(require_worker_token)]
