"""Persistent password epochs, idle fencing, global lockout and safe auth observations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.ui_auth import (
    AuthEvent,
    UiLoginState,
    UiSession,
    authenticate_owner,
    create_anonymous_session,
    get_ui_session,
    revoke_all_ui_sessions,
    unlock_owner_login,
)
from app.ui.auth import AuthSettings, token_digest

PASSWORD = "synthetic-password-for-auth-hardening"


@pytest.fixture(scope="module")
def settings() -> AuthSettings:
    return AuthSettings(password_hash=PasswordHasher().hash(PASSWORD))


@pytest.fixture
def configured(db_session: Session, settings: AuthSettings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", settings.password_hash)
    return db_session


def login(session: Session, settings: AuthSettings):
    challenge = create_anonymous_session(session, settings)
    outcome = authenticate_owner(session, challenge.token, challenge.csrf_token, PASSWORD, settings)
    session.commit()
    assert outcome.credentials is not None
    return outcome.credentials


def event_types(session: Session) -> list[str]:
    return list(session.scalars(select(AuthEvent.event_type).order_by(AuthEvent.created_at)))


def test_password_replacement_invalidates_existing_sessions(configured, settings, monkeypatch):
    credentials = login(configured, settings)
    assert get_ui_session(configured, credentials.token) is not None
    configured.commit()
    replacement = PasswordHasher().hash("another-synthetic-owner-password")
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", replacement)
    assert get_ui_session(configured, credentials.token) is None
    assert get_ui_session(configured, credentials.token, owner_required=False) is None


def test_idle_expiry_never_revives_and_activity_never_extends_absolute_expiry(configured, settings):
    credentials = login(configured, settings)
    digest = token_digest(credentials.token)
    configured.execute(
        update(UiSession)
        .where(UiSession.token_hash == digest)
        .values(last_active_at=func.clock_timestamp() - timedelta(minutes=10))
    )
    configured.commit()
    row = get_ui_session(configured, credentials.token)
    assert row is not None and row.expires_at == credentials.expires_at
    assert row.last_active_at > row.created_at
    configured.commit()
    configured.execute(
        update(UiSession)
        .where(UiSession.token_hash == digest)
        .values(last_active_at=func.clock_timestamp() - timedelta(minutes=31))
    )
    configured.commit()
    assert get_ui_session(configured, credentials.token) is None
    assert get_ui_session(configured, credentials.token) is None


def test_logout_everywhere_requires_current_owner_and_csrf(configured, settings):
    first = login(configured, settings)
    second = login(configured, settings)
    assert not revoke_all_ui_sessions(configured, first.token, "wrong", settings)
    configured.commit()
    assert get_ui_session(configured, second.token) is not None
    configured.commit()
    assert revoke_all_ui_sessions(configured, first.token, first.csrf_token, settings)
    configured.commit()
    assert get_ui_session(configured, first.token) is None
    assert get_ui_session(configured, second.token) is None
    assert not revoke_all_ui_sessions(configured, first.token, first.csrf_token, settings)
    assert event_types(configured).count("auth.sessions_revoked") == 1


def test_anonymous_session_cannot_revoke_owner_sessions(configured, settings):
    owner = login(configured, settings)
    anonymous = create_anonymous_session(configured, settings)
    configured.commit()
    assert not revoke_all_ui_sessions(configured, anonymous.token, anonymous.csrf_token, settings)
    assert get_ui_session(configured, owner.token) is not None


def test_twenty_failures_across_windows_lock_until_explicit_unlock(configured, settings):
    challenge = create_anonymous_session(configured, settings)
    configured.commit()
    for attempt in range(20):
        if attempt and attempt % 5 == 0:
            configured.execute(
                update(UiLoginState).values(
                    window_started_at=func.clock_timestamp() - timedelta(minutes=16)
                )
            )
            configured.commit()
        outcome = authenticate_owner(
            configured, challenge.token, challenge.csrf_token, "synthetic-wrong-password", settings
        )
        configured.commit()
        assert outcome.code == ("login_locked" if attempt == 19 else "invalid_credentials")
    configured.execute(
        update(UiLoginState).values(day_started_at=func.clock_timestamp() - timedelta(days=2))
    )
    configured.commit()
    configured.expire_all()
    assert (
        authenticate_owner(
            configured, challenge.token, challenge.csrf_token, PASSWORD, settings
        ).code
        == "login_locked"
    )
    configured.commit()
    assert event_types(configured).count("auth.login_failed") == 20
    assert event_types(configured).count("auth.login_locked") == 1
    unlock_owner_login(configured)
    configured.commit()
    assert authenticate_owner(
        configured, challenge.token, challenge.csrf_token, PASSWORD, settings
    ).success
    configured.commit()
    assert "auth.login_unlocked" in event_types(configured)


def test_repeated_throttled_polls_record_one_event_per_window(configured, settings):
    challenge = create_anonymous_session(configured, settings)
    configured.commit()
    for _ in range(5):
        authenticate_owner(
            configured, challenge.token, challenge.csrf_token, "synthetic-wrong-password", settings
        )
        configured.commit()
    for _ in range(10):
        outcome = authenticate_owner(
            configured, challenge.token, challenge.csrf_token, PASSWORD, settings
        )
        assert outcome.code == "login_throttled"
        configured.commit()
    assert event_types(configured).count("auth.login_throttled") == 1


def test_daily_budget_resets_only_when_not_locked(configured, settings):
    challenge = create_anonymous_session(configured, settings)
    configured.commit()
    configured.execute(
        update(UiLoginState).values(
            daily_failures=19, day_started_at=func.clock_timestamp() - timedelta(hours=25)
        )
    )
    configured.commit()
    outcome = authenticate_owner(
        configured, challenge.token, challenge.csrf_token, "synthetic-wrong-password", settings
    )
    configured.commit()
    assert outcome.code == "invalid_credentials"
    assert configured.get(UiLoginState, 1).daily_failures == 1


def test_concurrent_failures_cannot_exceed_shared_budget(configured, settings):
    challenge = create_anonymous_session(configured, settings)
    configured.commit()
    factory = sessionmaker(bind=configured.get_bind(), autoflush=False)

    def attempt():
        with factory() as session:
            outcome = authenticate_owner(
                session, challenge.token, challenge.csrf_token, "synthetic-wrong-password", settings
            )
            session.commit()
            return outcome.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(8)))
    assert outcomes.count("invalid_credentials") == 5
    assert outcomes.count("login_throttled") == 3
    assert configured.scalar(select(UiLoginState.daily_failures)) == 5


def test_auth_audit_contains_no_credentials_or_client_metadata(configured, settings):
    credentials = login(configured, settings)
    rows = configured.scalars(select(AuthEvent)).all()
    assert rows
    safe = repr([(row.event_type, row.created_at, row.affected_sessions) for row in rows])
    for secret in (PASSWORD, credentials.token, credentials.csrf_token, settings.password_hash):
        assert secret not in safe
    assert set(AuthEvent.__table__.columns.keys()) == {
        "id",
        "event_type",
        "created_at",
        "affected_sessions",
    }


def test_short_absolute_expiry_still_wins_over_idle_window(configured, settings):
    credentials = login(configured, replace(settings, session_lifetime=timedelta(seconds=1)))
    configured.execute(
        update(UiSession)
        .where(UiSession.token_hash == token_digest(credentials.token))
        .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
    )
    configured.commit()
    assert get_ui_session(configured, credentials.token) is None


def test_unlock_tool_uses_explicit_test_factory_and_records_event(configured):
    from scripts.unlock_login import main

    factory = sessionmaker(bind=configured.get_bind(), autoflush=False)
    assert main(["--target", "test", "--confirm-unlock"], session_factory=factory) == 0
    assert event_types(configured) == ["auth.login_unlocked"]
