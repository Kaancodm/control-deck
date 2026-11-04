"""Database-backed owner sessions, per-client login throttling, and a global daily lock."""

from __future__ import annotations

import hmac
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

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
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.ui.auth import (
    AuthSettings,
    SessionCredentials,
    client_fingerprint,
    get_auth_settings,
    issue_token,
    matches_token_hash,
    password_fingerprint,
    token_digest,
    verify_owner_password,
)


class UiSession(Base):
    """Only credential digests are persisted; revoked rows can be pruned safely."""

    __tablename__ = "ui_sessions"
    __table_args__ = (
        Index("ix_ui_sessions_expires_at", "expires_at"),
        Index("ix_ui_sessions_client_hash", "client_hash"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    password_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UiLoginState(Base):
    """A singleton row serializes logins and holds the global 24-hour budget and lock.

    Only explicit local administration via ``make unlock-login`` releases the lock.
    """

    __tablename__ = "ui_login_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ui_login_state_singleton"),
        CheckConstraint("daily_failures >= 0", name="ck_ui_login_state_daily_failures_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    daily_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    day_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UiLoginClient(Base):
    """One 15-minute failure window per connection peer, keyed by a digest of its address."""

    __tablename__ = "ui_login_clients"
    __table_args__ = (
        CheckConstraint("failures >= 0", name="ck_ui_login_clients_failures_nonnegative"),
        CheckConstraint("page_requests >= 0", name="ck_ui_login_clients_page_requests_nonnegative"),
        Index("ix_ui_login_clients_window_started_at", "window_started_at"),
    )

    client_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    throttled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    page_window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class AuthEvent(Base):
    """Taskless authentication observations contain no credentials or client identifiers."""

    __tablename__ = "auth_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('auth.login_failed', 'auth.login_throttled', 'auth.login_locked', "
            "'auth.login_succeeded', 'auth.logout', 'auth.sessions_revoked', "
            "'auth.login_unlocked')",
            name="ck_auth_events_type",
        ),
        CheckConstraint("affected_sessions >= 0", name="ck_auth_events_count"),
        Index("ix_auth_events_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    affected_sessions: Mapped[int] = mapped_column(Integer, nullable=False)


def _audit(session: Session, event_type: str, *, affected_sessions: int = 0) -> None:
    session.add(AuthEvent(event_type=event_type, affected_sessions=affected_sessions))


class SessionCapacityError(ValueError):
    """Anonymous sessions are bounded independently of password authentication."""

    code = "login_capacity"

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__("Owner login is temporarily unavailable.")


@dataclass(frozen=True)
class LoginOutcome:
    """Return failed logins normally so callers commit the failure budgets."""

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
        .values(id=1, daily_failures=0, day_started_at=func.clock_timestamp())
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


def _lock_client_window(session: Session, client_hash: str) -> UiLoginClient:
    """Callers hold the singleton lock first, so this never deadlocks with pruning."""
    session.execute(
        insert(UiLoginClient)
        .values(client_hash=client_hash, failures=0, window_started_at=func.clock_timestamp())
        .on_conflict_do_nothing(index_elements=["client_hash"])
    )
    window = session.scalar(
        select(UiLoginClient)
        .where(UiLoginClient.client_hash == client_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert window is not None
    return window


def _issue_session(
    session: Session,
    *,
    settings: AuthSettings,
    authenticated: bool,
    now: datetime,
    client_hash: str | None = None,
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
            last_active_at=now,
            password_fingerprint=password_fingerprint(settings) if authenticated else None,
            client_hash=client_hash,
        )
    )
    session.flush()
    return credentials


def create_anonymous_session(
    session: Session, settings: AuthSettings, *, client: str | None = None
) -> SessionCredentials:
    """Issue a short-lived login/CSRF challenge; caller owns the transaction."""
    _lock_login_state(session)
    now = _database_now(session)
    session.execute(
        delete(UiSession).where((UiSession.expires_at <= now) | UiSession.revoked_at.is_not(None))
    )
    # Elapsed client windows carry no protection any more; pruning keeps the table bounded.
    session.execute(
        delete(UiLoginClient).where(
            (UiLoginClient.window_started_at <= now - settings.failure_window)
            & (
                (UiLoginClient.page_requests == 0)
                | (UiLoginClient.page_window_started_at <= now - settings.login_page_window)
            )
        )
    )
    count = session.scalar(
        select(func.count()).select_from(UiSession).where(UiSession.authenticated.is_(False))
    )
    if count is None or count >= settings.max_anonymous_sessions:
        raise SessionCapacityError(math.ceil(settings.anonymous_lifetime.total_seconds()))
    digest = client_fingerprint(client)
    client_count = session.scalar(
        select(func.count())
        .select_from(UiSession)
        .where(UiSession.authenticated.is_(False), UiSession.client_hash == digest)
    )
    if client_count is None or client_count >= settings.max_anonymous_sessions_per_client:
        raise SessionCapacityError(math.ceil(settings.anonymous_lifetime.total_seconds()))
    return _issue_session(
        session, settings=settings, authenticated=False, now=now, client_hash=digest
    )


def limit_login_page(session: Session, settings: AuthSettings, *, client: str | None) -> int | None:
    """Count GETs, including cookie reuse, and return a bounded retry delay when limited."""
    _lock_login_state(session)
    window = _lock_client_window(session, client_fingerprint(client))
    now = _database_now(session)
    ends = window.page_window_started_at + settings.login_page_window
    if now >= ends:
        window.page_requests = 0
        window.page_window_started_at = now
        ends = now + settings.login_page_window
    if window.page_requests >= settings.max_login_page_requests:
        return max(1, math.ceil((ends - now).total_seconds()))
    window.page_requests += 1
    session.flush()
    return None


def get_ui_session(
    session: Session,
    token: str | None,
    *,
    owner_required: bool = True,
    for_update: bool = False,
    settings: AuthSettings | None = None,
    touch_activity: bool = True,
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
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None or not row.authenticated:
        return row
    current = settings or get_auth_settings()
    now = _database_now(session)
    fingerprint = password_fingerprint(current)
    if (
        not hmac.compare_digest(row.password_fingerprint or "", fingerprint)
        or row.last_active_at <= now - current.idle_lifetime
    ):
        return None
    # Anonymous/login resolution must not take a session write lock before the
    # singleton login lock. Owner requests may extend idle time, never absolute expiry.
    if owner_required and touch_activity:
        return session.scalar(
            update(UiSession)
            .where(
                UiSession.token_hash == row.token_hash,
                UiSession.revoked_at.is_(None),
                UiSession.expires_at > func.clock_timestamp(),
                UiSession.password_fingerprint == fingerprint,
                UiSession.last_active_at > func.clock_timestamp() - current.idle_lifetime,
            )
            .values(last_active_at=func.clock_timestamp())
            .returning(UiSession)
            .execution_options(populate_existing=True)
        )
    return row


def check_csrf(row: UiSession, provided: str | None) -> bool:
    """Only an explicitly submitted token passes; a request cookie alone is insufficient."""
    return matches_token_hash(provided, row.csrf_hash)


def owner_login_locked(session: Session) -> bool:
    """Expose the global lock without acquiring a session or administration lock."""
    return session.scalar(select(UiLoginState.locked_at).where(UiLoginState.id == 1)) is not None


def _check_login(
    session: Session,
    token: str | None,
    csrf: str | None,
    settings: AuthSettings,
    *,
    client: str | None = None,
) -> LoginOutcome | tuple[UiLoginState, UiSession, UiLoginClient]:
    """Revalidate under the singleton -> challenge -> client lock order."""
    state = _lock_login_state(session)
    row = get_ui_session(session, token, owner_required=False, for_update=True, settings=settings)
    if row is None:
        return LoginOutcome("session_required")
    if not check_csrf(row, csrf):
        return LoginOutcome("invalid_csrf")
    now = _database_now(session)
    if state.locked_at is not None:
        return LoginOutcome("login_locked")
    if now >= state.day_started_at + settings.daily_failure_window:
        state.daily_failures = 0
        state.day_started_at = now
    if state.daily_failures >= settings.max_daily_failures:
        state.locked_at = now
        _audit(session, "auth.login_locked")
        return LoginOutcome("login_locked")
    window = _lock_client_window(session, client_fingerprint(client))
    window_ends = window.window_started_at + settings.failure_window
    if now >= window_ends:
        window.failures = 0
        window.window_started_at = now
        window.throttled_at = None
        window_ends = now + settings.failure_window
    if window.failures >= settings.max_failures:
        if window.throttled_at is None:
            window.throttled_at = now
            _audit(session, "auth.login_throttled")
        return LoginOutcome(
            "login_throttled", retry_after=max(1, math.ceil((window_ends - now).total_seconds()))
        )
    return state, row, window


def authenticate_owner(
    session: Session,
    token: str | None,
    csrf: str | None,
    password: str,
    settings: AuthSettings,
    *,
    client: str | None = None,
) -> LoginOutcome:
    """Verify without a database transaction, then recheck all protection state.

    This operation commits its short preflight transaction before Argon2; callers
    must use a dedicated request Session (not an enclosing ``Session.begin``).
    They commit the final outcome, including failures, before returning a response.
    Only digests of connection peers and credentials are persisted.
    """
    checked = _check_login(session, token, csrf, settings, client=client)
    if isinstance(checked, LoginOutcome):
        return checked
    session.commit()
    verified = verify_owner_password(password, settings)
    checked = _check_login(session, token, csrf, settings, client=client)
    if isinstance(checked, LoginOutcome):
        return checked
    state, row, window = checked
    now = _database_now(session)
    if not verified:
        window.failures += 1
        state.daily_failures += 1
        _audit(session, "auth.login_failed")
        if state.daily_failures >= settings.max_daily_failures:
            state.locked_at = now
            _audit(session, "auth.login_locked")
            session.flush()
            return LoginOutcome("login_locked")
        session.flush()
        return LoginOutcome("invalid_credentials")
    now = _database_now(session)
    # A long verification must not revive a challenge that expired meanwhile.
    if row.expires_at <= now:
        return LoginOutcome("session_required")
    window.failures = 0
    window.window_started_at = now
    window.throttled_at = None
    row.revoked_at = now
    credentials = _issue_session(session, settings=settings, authenticated=True, now=now)
    _audit(session, "auth.login_succeeded")
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
        _audit(session, "auth.logout", affected_sessions=1)
        session.flush()


def revoke_all_ui_sessions(
    session: Session, token: str | None, csrf: str | None, settings: AuthSettings
) -> bool:
    """Authenticate and revoke under the login -> session lock order."""
    _lock_login_state(session)
    owner = get_ui_session(session, token, for_update=True, settings=settings, touch_activity=False)
    if owner is None or not check_csrf(owner, csrf):
        return False
    tokens = session.scalars(
        update(UiSession)
        .where(UiSession.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp())
        .returning(UiSession.token_hash)
    ).all()
    _audit(session, "auth.sessions_revoked", affected_sessions=len(tokens))
    session.flush()
    return True


def observe_owner_login_state_on_startup(session: Session) -> None:
    """Only observe startup state: restarts never renew budgets or release locks."""
    locked = owner_login_locked(session)
    logging.getLogger(__name__).info("Owner login startup: locked=%s", locked)


def unlock_owner_login(session: Session) -> None:
    """Explicit local administration; resets lock, budgets and windows, not the password."""
    state = _lock_login_state(session)
    now = _database_now(session)
    state.daily_failures = 0
    state.day_started_at = now
    state.locked_at = None
    session.execute(delete(UiLoginClient))
    _audit(session, "auth.login_unlocked")
    session.flush()
