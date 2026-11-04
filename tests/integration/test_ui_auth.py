"""Persisted owner sessions, login rotation, CSRF, expiry, per-client throttling, global lock."""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine
from app.db.ui_auth import (
    AuthEvent,
    SessionCapacityError,
    UiLoginClient,
    UiLoginState,
    UiSession,
    authenticate_owner,
    check_csrf,
    create_anonymous_session,
    get_ui_session,
    observe_owner_login_state_on_startup,
    owner_login_locked,
    revoke_ui_session,
)
from app.ui.auth import AuthSettings, SessionCredentials, issue_token, token_digest

_PASSWORD = "synthetic-owner-password-for-integration-tests"
_CLIENT_A = "synthetic-client-a"
_CLIENT_B = "synthetic-client-b"


@pytest.fixture(scope="module")
def auth_settings() -> AuthSettings:
    return AuthSettings(password_hash=PasswordHasher().hash(_PASSWORD))


@pytest.fixture
def auth_session(
    db_session: Session, auth_settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> Generator[Session, None, None]:
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", auth_settings.password_hash)
    for table in (UiSession, UiLoginClient, UiLoginState, AuthEvent):
        db_session.execute(delete(table))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.rollback()
        for table in (UiSession, UiLoginClient, UiLoginState, AuthEvent):
            db_session.execute(delete(table))
        db_session.commit()


def _attempt(
    session: Session,
    challenge: SessionCredentials,
    settings: AuthSettings,
    password: str,
    *,
    client: str,
) -> str:
    """Commit one login attempt from ``client`` and return its outcome code."""
    outcome = authenticate_owner(
        session, challenge.token, challenge.csrf_token, password, settings, client=client
    )
    session.commit()
    return outcome.code


def _event_types(session: Session) -> list[str]:
    return list(session.scalars(select(AuthEvent.event_type).order_by(AuthEvent.created_at)))


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
    assert auth_session.scalar(select(UiLoginState.daily_failures)) == 0
    assert auth_session.scalar(select(func.count()).select_from(UiLoginClient)) == 0
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


def test_failure_window_of_one_client_is_shared_across_browsers_and_database_sessions(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=2)
    first = create_anonymous_session(auth_session, settings)
    second = create_anonymous_session(auth_session, settings)
    auth_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True))
    for challenge in (first, second):
        with factory() as session:
            assert _attempt(session, challenge, settings, "wrong", client=_CLIENT_A) == (
                "invalid_credentials"
            )
    outcome = authenticate_owner(
        auth_session, first.token, first.csrf_token, _PASSWORD, settings, client=_CLIENT_A
    )
    assert outcome.code == "login_throttled" and not outcome.success
    assert outcome.retry_after is not None and outcome.retry_after > 0
    assert auth_session.scalar(select(UiLoginClient.failures)) == 2
    assert auth_session.scalar(select(func.count()).select_from(UiLoginClient)) == 1


def test_throttle_is_per_client(auth_session: Session, auth_settings: AuthSettings) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()
    for _ in range(auth_settings.max_failures):
        assert _attempt(auth_session, challenge, auth_settings, "wrong", client=_CLIENT_A) == (
            "invalid_credentials"
        )
    throttled = authenticate_owner(
        auth_session,
        challenge.token,
        challenge.csrf_token,
        _PASSWORD,
        auth_settings,
        client=_CLIENT_A,
    )
    auth_session.commit()
    assert throttled.code == "login_throttled" and not throttled.success
    assert throttled.retry_after is not None
    assert 0 < throttled.retry_after <= auth_settings.failure_window.total_seconds()
    assert _attempt(auth_session, challenge, auth_settings, "wrong", client=_CLIENT_B) == (
        "invalid_credentials"
    )
    success = authenticate_owner(
        auth_session,
        challenge.token,
        challenge.csrf_token,
        _PASSWORD,
        auth_settings,
        client=_CLIENT_B,
    )
    auth_session.commit()
    assert success.success
    assert not owner_login_locked(auth_session)
    # The daily budget stays global while the 15-minute window is per client.
    expected_daily = auth_settings.max_failures + 1
    assert auth_session.scalar(select(UiLoginState.daily_failures)) == expected_daily
    hashes = auth_session.scalars(select(UiLoginClient.client_hash)).all()
    assert len(hashes) == 2
    assert all(_CLIENT_A not in value and _CLIENT_B not in value for value in hashes)
    assert _event_types(auth_session).count("auth.login_throttled") == 1


def test_daily_lock_is_global_across_clients(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=2, max_daily_failures=4)
    challenge = create_anonymous_session(auth_session, settings)
    auth_session.commit()
    assert _attempt(auth_session, challenge, settings, "wrong", client=_CLIENT_A) == (
        "invalid_credentials"
    )
    assert _attempt(auth_session, challenge, settings, "wrong", client=_CLIENT_A) == (
        "invalid_credentials"
    )
    assert _attempt(auth_session, challenge, settings, "wrong", client=_CLIENT_B) == (
        "invalid_credentials"
    )
    assert _attempt(auth_session, challenge, settings, "wrong", client=_CLIENT_B) == "login_locked"
    assert owner_login_locked(auth_session)
    assert _attempt(auth_session, challenge, settings, _PASSWORD, client="synthetic-client-c") == (
        "login_locked"
    )
    assert _event_types(auth_session).count("auth.login_locked") == 1
    assert _event_types(auth_session).count("auth.login_failed") == 4


def test_restart_preserves_twenty_failures_and_global_lock(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = auth_settings
    challenge = create_anonymous_session(auth_session, settings)
    auth_session.commit()
    for index in range(20):
        code = _attempt(
            auth_session, challenge, settings, "wrong", client=f"synthetic-client-{index // 5}"
        )
    assert code == "login_locked"
    assert owner_login_locked(auth_session)
    before = auth_session.execute(
        select(UiLoginState.daily_failures, UiLoginState.day_started_at, UiLoginState.locked_at)
    ).one()
    observe_owner_login_state_on_startup(auth_session)
    auth_session.commit()
    assert (
        auth_session.execute(
            select(UiLoginState.daily_failures, UiLoginState.day_started_at, UiLoginState.locked_at)
        ).one()
        == before
    )
    assert _attempt(auth_session, challenge, settings, _PASSWORD, client=_CLIENT_A) == (
        "login_locked"
    )
    events = _event_types(auth_session)
    assert events.count("auth.login_locked") == 1
    assert events.count("auth.login_unlocked") == 0


def test_password_verification_releases_transaction_and_rechecks_lock(
    auth_session: Session, auth_settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()

    def verify(password, settings):
        assert not auth_session.in_transaction()
        # A separate connection can lock every login row while Argon2 is running.
        with Session(auth_session.get_bind()) as concurrent:
            concurrent.execute(select(UiSession).with_for_update(nowait=True)).all()
            state = concurrent.scalar(select(UiLoginState).with_for_update(nowait=True))
            concurrent.execute(select(UiLoginClient).with_for_update(nowait=True)).all()
            state.daily_failures = 20
            state.locked_at = concurrent.scalar(select(func.clock_timestamp()))
            concurrent.commit()
        return True

    monkeypatch.setattr("app.db.ui_auth.verify_owner_password", verify)
    assert _attempt(auth_session, challenge, auth_settings, _PASSWORD, client=_CLIENT_A) == (
        "login_locked"
    )
    assert auth_session.scalar(select(UiLoginState.daily_failures)) == 20
    assert "auth.login_succeeded" not in _event_types(auth_session)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("revoked", "session_required"),
        ("expired", "session_required"),
        ("client_budget", "login_throttled"),
        ("daily_budget", "login_locked"),
    ],
)
def test_verification_rechecks_concurrent_changes(
    auth_session, auth_settings, monkeypatch, change, expected
):
    challenge = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()

    def verify(password, settings):
        assert not auth_session.in_transaction()
        with Session(auth_session.get_bind()) as concurrent:
            if change == "revoked":
                statement = update(UiSession).values(revoked_at=func.clock_timestamp())
            elif change == "expired":
                statement = update(UiSession).values(expires_at=func.clock_timestamp())
            elif change == "client_budget":
                statement = update(UiLoginClient).values(failures=settings.max_failures)
            else:
                statement = update(UiLoginState).values(daily_failures=settings.max_daily_failures)
            concurrent.execute(statement)
            concurrent.commit()
        return True

    monkeypatch.setattr("app.db.ui_auth.verify_owner_password", verify)
    assert _attempt(auth_session, challenge, auth_settings, _PASSWORD, client=_CLIENT_A) == expected
    assert "auth.login_succeeded" not in _event_types(auth_session)


def test_concurrent_failures_cannot_exceed_limit(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=1)
    challenges = [create_anonymous_session(auth_session, settings) for _ in range(2)]
    auth_session.commit()
    factory = sessionmaker(bind=get_engine(for_tests=True))

    def try_login(index: int) -> str:
        with factory() as session:
            return _attempt(session, challenges[index], settings, "wrong", client=_CLIENT_A)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(try_login, (0, 1)))
    assert sorted(codes) == ["invalid_credentials", "login_throttled"]
    assert auth_session.scalar(select(UiLoginClient.failures)) == 1


def test_failure_window_expires_using_database_time(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    settings = replace(auth_settings, max_failures=1)
    challenge = create_anonymous_session(auth_session, settings)
    authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, "wrong", settings, client=_CLIENT_A
    )
    window = auth_session.scalar(select(UiLoginClient))
    assert window is not None
    window.window_started_at -= settings.failure_window + timedelta(seconds=1)
    auth_session.commit()
    outcome = authenticate_owner(
        auth_session, challenge.token, challenge.csrf_token, _PASSWORD, settings, client=_CLIENT_A
    )
    assert outcome.success
    assert auth_session.scalar(select(UiLoginClient.failures)) == 0


def test_expired_client_windows_are_pruned_with_expired_sessions(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()
    assert _attempt(auth_session, challenge, auth_settings, "wrong", client=_CLIENT_A) == (
        "invalid_credentials"
    )
    window = auth_session.scalar(select(UiLoginClient))
    assert window is not None
    window.window_started_at -= auth_settings.failure_window + timedelta(seconds=1)
    auth_session.commit()
    create_anonymous_session(auth_session, auth_settings)
    assert auth_session.scalar(select(func.count()).select_from(UiLoginClient)) == 0


def test_success_resets_failures_and_second_login_rotates_again(
    auth_session: Session, auth_settings: AuthSettings
) -> None:
    challenge = create_anonymous_session(auth_session, auth_settings)
    auth_session.commit()
    assert _attempt(auth_session, challenge, auth_settings, "wrong", client=_CLIENT_A) == (
        "invalid_credentials"
    )
    first = authenticate_owner(
        auth_session,
        challenge.token,
        challenge.csrf_token,
        _PASSWORD,
        auth_settings,
        client=_CLIENT_A,
    )
    assert first.credentials is not None
    assert auth_session.scalar(select(UiLoginClient.failures)) == 0
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
