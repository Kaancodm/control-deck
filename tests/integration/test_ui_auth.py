"""Persisted owner sessions, login rotation, CSRF, expiry, and shared throttling."""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine
from app.db.ui_auth import (
    SessionCapacityError,
    UiLoginState,
    UiSession,
    authenticate_owner,
    check_csrf,
    create_anonymous_session,
    get_ui_session,
    revoke_ui_session,
)
from app.ui.auth import AuthSettings, issue_token, token_digest

_PASSWORD = "synthetic-owner-password-for-integration-tests"


@pytest.fixture(scope="module")
def auth_settings() -> AuthSettings:
    return AuthSettings(password_hash=PasswordHasher().hash(_PASSWORD))


@pytest.fixture
def auth_session(
    db_session: Session, auth_settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> Generator[Session, None, None]:
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", auth_settings.password_hash)
    db_session.execute(delete(UiSession))
    db_session.execute(delete(UiLoginState))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.rollback()
        db_session.execute(delete(UiSession))
        db_session.execute(delete(UiLoginState))
        db_session.commit()


def test_anonymous_challenge_cannot_authenticate_owner(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    credentials = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()
    assert get_ui_session(auth_session, credentials.token) is None
    row = get_ui_session(auth_session, credentials.token, owner_required=False)
    assert row is not None and check_csrf(row, credentials.csrf_token)
    assert row.token_hash == token_digest(credentials.token)
    assert row.csrf_hash == token_digest(credentials.csrf_token)
    assert credentials.token not in str(row.__dict__)
    assert credentials.csrf_token not in str(row.__dict__)
    assert row.expires_at - row.created_at == auth_settings.anonymous_lifetime


def test_success_rotates_session_and_csrf_and_revokes_original(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, auth_settings
    )
    auth_session.commit()
    assert outcome.success and outcome.credentials is not None
    credentials = outcome.credentials
    assert credentials.token != challenge.token
    assert credentials.csrf_token != challenge.csrf_token
    assert get_ui_session(auth_session, challenge.token, owner_required=False) is None
    row = get_ui_session(auth_session, credentials.token)
    assert row is not None and row.authenticated
    assert check_csrf(row, credentials.csrf_token)
    assert not check_csrf(row, challenge.csrf_token)
    assert row.expires_at - row.created_at == auth_settings.session_lifetime


@pytest.mark.parametrize("csrf", [None, "", "invalid", "x" * 43])
def test_wrong_or_missing_csrf_never_authenticates_or_consumes_password_budget(
    auth_session: Session, auth_settings: AuthSettings, csrf: str | None
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    outcome = authenticate_owner(auth_session, challenge.token, csrf, _PASSWORD, auth_settings)
    auth_session.commit()
    assert outcome.code == "invalid_csrf" and not outcome.success
    assert auth_session.scalar(select(UiLoginState.failures)) == 0
    assert auth_session.scalar(select(func.count()).select_from(UiSession)) == 1


def test_csrf_from_another_browser_does_not_work(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    first = create_anonymous_session(auth_session, auth_settings)
    second = create_anonymous_session(auth_session, auth_settings)
    outcome = authenticate_owner(
        auth_session, first.token, second.csrf_token, _PASSWORD, auth_settings
    )
    assert outcome.code == "invalid_csrf"


def test_expired_challenge_cannot_be_used(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    row = get_ui_session(auth_session, challenge.token, owner_required=False)
    assert row is not None
    row.expires_at = row.created_at - timedelta(seconds=1)
    auth_session.commit()
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, auth_settings
    )
    assert outcome.code == "session_required"


def test_expiry_and_revocation_apply_across_database_sessions(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, auth_settings
    )
    assert outcome.credentials is not None
    credentials = outcome.credentials
    auth_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True))
    with factory() as other:
        assert get_ui_session(other, credentials.token) is not None
        revoke_ui_session(other, credentials.token)
        other.commit()
    assert get_ui_session(auth_session, credentials.token) is None
    assert get_ui_session(auth_session, issue_token()) is None


def test_absolute_owner_session_expiry_does_not_slide(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, auth_settings
    )
    assert outcome.credentials is not None
    row = get_ui_session(auth_session, outcome.credentials.token)
    assert row is not None
    expiry = row.expires_at
    assert get_ui_session(auth_session, outcome.credentials.token) is not None
    assert row.expires_at == expiry
    row.expires_at = row.created_at - timedelta(seconds=1)
    auth_session.commit()
    assert get_ui_session(auth_session, outcome.credentials.token) is None


def test_failure_budget_is_shared_between_browsers_and_database_sessions(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=2)
    first = create_anonymous_session(auth_session, settings)
    second = create_anonymous_session(auth_session, settings)
    auth_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True))
    for challenge in (first, second):
        with factory() as session:
            outcome = authenticate_owner(
                session, challenge.token, challenge.csrf_token, "wrong", settings
            )
            session.commit()
            assert outcome.code == "invalid_credentials"
    outcome = authenticate_owner(auth_session, first.token, first.csrf_token, _PASSWORD, settings)
    assert outcome.code == "login_throttled" and not outcome.success
    assert outcome.retry_after is not None and outcome.retry_after > 0
    assert auth_session.scalar(select(UiLoginState.failures)) == 2


def test_concurrent_failures_cannot_exceed_limit(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=1)
    challenges = [create_anonymous_session(auth_session, settings) for _ in range(2)]
    auth_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True))

    def try_login(index: int) -> str:
        challenge = challenges[index]
        with factory() as session:
            outcome = authenticate_owner(
                session, challenge.token, challenge.csrf_token, "wrong", settings
            )
            session.commit()
            return outcome.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(try_login, (0, 1)))
    assert sorted(codes) == ["invalid_credentials", "login_throttled"]
    assert auth_session.scalar(select(UiLoginState.failures)) == 1


def test_failure_window_expires_using_database_time(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=1)
    challenge = create_anonymous_session(auth_session, settings)
    authenticate_owner(auth_session, challenge.token, challenge.csrf_token, "wrong", settings)
    state = auth_session.get(UiLoginState, 1)
    assert state is not None
    state.window_started_at -= settings.failure_window + timedelta(seconds=1)
    auth_session.commit()
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, settings
    )
    assert outcome.success
    assert auth_session.scalar(select(UiLoginState.failures)) == 0


def test_success_resets_failures_and_second_login_rotates_again(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    authenticate_owner(auth_session, challenge.token, challenge.csrf_token, "wrong", auth_settings)
    first = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, auth_settings
    )
    assert first.credentials is not None
    assert auth_session.scalar(select(UiLoginState.failures)) == 0
    second = authenticate_owner(
        auth_session,
        first.credentials.token,
        first.credentials.csrf_token,
        _PASSWORD,
        auth_settings,
    )
    assert second.success and second.credentials is not None
    assert second.credentials.token != first.credentials.token
    assert get_ui_session(auth_session, first.credentials.token) is None


def test_anonymous_capacity_is_bounded_and_expired_rows_are_pruned(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_anonymous_sessions=1)
    first = create_anonymous_session(auth_session, settings)
    with pytest.raises(SessionCapacityError):
        create_anonymous_session(auth_session, settings)
    row = get_ui_session(auth_session, first.token, owner_required=False)
    assert row is not None
    row.expires_at = row.created_at - timedelta(seconds=1)
    auth_session.flush()
    second = create_anonymous_session(auth_session, settings)
    assert second.token != first.token
    assert auth_session.scalar(select(func.count()).select_from(UiSession)) == 1


def test_revoked_anonymous_rows_release_capacity(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_anonymous_sessions=1)
    first = create_anonymous_session(auth_session, settings)
    revoke_ui_session(auth_session, first.token)
    second = create_anonymous_session(auth_session, settings)
    assert get_ui_session(auth_session, second.token, owner_required=False) is not None
    assert auth_session.scalar(select(func.count()).select_from(UiSession)) == 1
