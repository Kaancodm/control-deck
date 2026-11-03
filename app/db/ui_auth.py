"""Database-backed owner sessions and a shared, bounded login failure budget."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.ui.auth import (
    AuthSettings,
    SessionCredentials,
    issue_token,
    matches_token_hash,
    token_digest,
    verify_owner_password,
)


class UiSession(Base):
    """Only credential digests are persisted; revoked rows can be pruned safely."""

    __tablename__ = "ui_sessions"
    __table_args__ = (Index("ix_ui_sessions_expires_at", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UiLoginState(Base):
    """A singleton row serializes the one owner account's failure window."""

    __tablename__ = "ui_login_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ui_login_state_singleton"),
        CheckConstraint("failures >= 0", name="ck_ui_login_state_failures_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionCapacityError(ValueError):
    """Anonymous sessions are bounded independently of password authentication."""

    code = "login_capacity"

    def __init__(self) -> None:
        super().__init__("Owner login is temporarily unavailable.")


@dataclass(frozen=True)
class LoginOutcome:
    """Return failed logins normally so callers commit the shared failure budget."""

    code: str
    credentials: SessionCredentials | None = None
    retry_after: int | None = None

    @property
    def success(self) -> bool:
        return self.credentials is not None


def _database_now(session: Session) -> datetime:
    now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise RuntimeError("Database time is unavailable.")
    return now


def _lock_login_state(session: Session) -> UiLoginState:
    session.execute(
        insert(UiLoginState)
        .values(id=1, failures=0, window_started_at=func.clock_timestamp())
        .on_conflict_do_nothing(index_elements=["id"])
    )
    state = session.scalar(
        select(UiLoginState)
        .where(UiLoginState.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert state is not None
    return state


def _issue_session(
    session: Session, *, settings: AuthSettings, authenticated: bool, now: datetime
) -> SessionCredentials:
    credentials = SessionCredentials(
        token=issue_token(),
        csrf_token=issue_token(),
        expires_at=now
        + (settings.session_lifetime if authenticated else settings.anonymous_lifetime),
    )
    digest = token_digest(credentials.token)
    csrf_digest = token_digest(credentials.csrf_token)
    assert digest is not None and csrf_digest is not None
    session.add(
        UiSession(
            token_hash=digest,
            csrf_hash=csrf_digest,
            authenticated=authenticated,
            created_at=now,
            expires_at=credentials.expires_at,
        )
    )
    session.flush()
    return credentials


def create_anonymous_session(session: Session, settings: AuthSettings) -> SessionCredentials:
    """Issue a short-lived login/CSRF challenge; caller owns the transaction."""
    _lock_login_state(session)
    now = _database_now(session)
    session.execute(
        delete(UiSession).where((UiSession.expires_at <= now) | UiSession.revoked_at.is_not(None))
    )
    count = session.scalar(
        select(func.count()).select_from(UiSession).where(UiSession.authenticated.is_(False))
    )
    if count is None or count >= settings.max_anonymous_sessions:
        raise SessionCapacityError()
    return _issue_session(session, settings=settings, authenticated=False, now=now)


def get_ui_session(
    session: Session,
    token: str | None,
    *,
    owner_required: bool = True,
    for_update: bool = False,
) -> UiSession | None:
    """Resolve an unexpired, unrevoked session using database time, never browser time."""
    digest = token_digest(token)
    if digest is None:
        return None
    query = select(UiSession).where(
        UiSession.token_hash == digest,
        UiSession.revoked_at.is_(None),
        UiSession.expires_at > func.clock_timestamp(),
    )
    if owner_required:
        query = query.where(UiSession.authenticated.is_(True))
    if for_update:
        query = query.with_for_update()
    return session.scalar(query.execution_options(populate_existing=True))


def check_csrf(row: UiSession, provided: str | None) -> bool:
    """Only an explicitly submitted token passes; a request cookie alone is insufficient."""
    return matches_token_hash(provided, row.csrf_hash)


def authenticate_owner(
    session: Session,
    token: str | None,
    csrf: str | None,
    password: str,
    settings: AuthSettings,
) -> LoginOutcome:
    """Rotate a valid login challenge after Argon2id verification.

    Every outcome must be committed before returning an HTTP response. No
    password, raw cookie, or CSRF token is persisted. Taking the singleton lock
    before any session lock prevents concurrent logins from exceeding the cap.
    """
    state = _lock_login_state(session)
    row = get_ui_session(session, token, owner_required=False, for_update=True)
    if row is None:
        return LoginOutcome("session_required")
    if not check_csrf(row, csrf):
        return LoginOutcome("invalid_csrf")
    now = _database_now(session)
    window_ends = state.window_started_at + settings.failure_window
    if now >= window_ends:
        state.failures = 0
        state.window_started_at = now
        window_ends = now + settings.failure_window
    if state.failures >= settings.max_failures:
        return LoginOutcome(
            "login_throttled", retry_after=max(1, math.ceil((window_ends - now).total_seconds()))
        )
    if not verify_owner_password(password, settings):
        state.failures += 1
        session.flush()
        return LoginOutcome("invalid_credentials")
    now = _database_now(session)
    # A long verification must not revive a challenge that expired meanwhile.
    if row.expires_at <= now:
        return LoginOutcome("session_required")
    state.failures = 0
    state.window_started_at = now
    row.revoked_at = now
    credentials = _issue_session(session, settings=settings, authenticated=True, now=now)
    return LoginOutcome("authenticated", credentials=credentials)


def revoke_ui_session(session: Session, token: str | None) -> None:
    """Invalidate a browser session immediately; callers enforce owner auth and CSRF."""
    digest = token_digest(token)
    if digest is None:
        return
    row = session.scalar(
        select(UiSession)
        .where(UiSession.token_hash == digest)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = _database_now(session)
        session.flush()
