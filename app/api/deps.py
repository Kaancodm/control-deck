"""FastAPI dependencies (database session, auth placeholders)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.config import get_worker_token, worker_token_configured
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


def require_owner() -> None:
    """
    Placeholder for owner authentication (Etappe 15).

    Currently allows all requests; future versions will validate login sessions.
    """


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
) -> None:
    """Validate worker access via ``Authorization: Bearer`` or ``X-Deck-Worker-Token``."""
    if not worker_token_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker authentication is not configured (DECK_WORKER_TOKEN)",
        )
    expected = get_worker_token()
    provided = _extract_bearer(authorization) or (x_deck_worker_token or "").strip() or None
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing worker token",
        )


WorkerAuth = Annotated[None, Depends(require_worker_token)]
