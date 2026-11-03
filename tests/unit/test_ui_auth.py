"""Argon2id settings, opaque credentials, and secure UI cookie primitives."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher, Type
from starlette.requests import Request
from starlette.responses import Response

from app.ui.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthConfigurationError,
    AuthSettings,
    SessionCredentials,
    clear_session_cookies,
    client_address,
    client_fingerprint,
    get_auth_settings,
    issue_token,
    matches_token_hash,
    set_session_cookies,
    token_digest,
    verify_owner_password,
)

_PASSWORD = "synthetic-owner-password-for-tests"


@pytest.fixture(scope="module")
def settings() -> AuthSettings:
    return AuthSettings(password_hash=PasswordHasher().hash(_PASSWORD))


def test_config_requires_explicit_argon2id_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECK_OWNER_PASSWORD_HASH", raising=False)
    with pytest.raises(AuthConfigurationError) as failure:
        get_auth_settings()
    assert failure.value.code == "owner_password_unconfigured"


@pytest.mark.parametrize("value", ["plaintext-secret", "", "$argon2id$broken", "x" * 513])
def test_invalid_hash_never_appears_in_configuration_error(value: str) -> None:
    with pytest.raises(AuthConfigurationError) as failure:
        AuthSettings(password_hash=value)
    assert str(failure.value) == "Owner login configuration is unavailable."


@pytest.mark.parametrize("hash_type", [Type.I, Type.D])
def test_other_argon2_variants_are_rejected(hash_type: Type) -> None:
    with pytest.raises(AuthConfigurationError):
        AuthSettings(password_hash=PasswordHasher(type=hash_type).hash(_PASSWORD))


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_cost": 1024},
        {"time_cost": 1},
        {"salt_len": 8},
        {"hash_len": 8},
    ],
)
def test_weak_argon2id_parameters_are_rejected(changes: dict[str, int]) -> None:
    with pytest.raises(AuthConfigurationError):
        AuthSettings(password_hash=PasswordHasher(**changes).hash(_PASSWORD))


def test_password_verification_and_redacted_representations(settings: AuthSettings) -> None:
    assert verify_owner_password(_PASSWORD, settings)
    assert not verify_owner_password("incorrect-synthetic-password", settings)
    assert _PASSWORD not in repr(settings)
    assert settings.password_hash not in repr(settings)


@pytest.mark.parametrize("password", ["", "x" * 1025, "é" * 513, "\ud800"])
def test_password_input_is_bounded(settings: AuthSettings, password: str) -> None:
    assert not verify_owner_password(password, settings)


def test_secure_cookies_are_default_with_explicit_http_test_opt_out(
    settings: AuthSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", settings.password_hash)
    monkeypatch.delenv("DECK_UI_COOKIE_SECURE", raising=False)
    assert get_auth_settings().cookie_secure
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    assert not get_auth_settings().cookie_secure
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "maybe")
    with pytest.raises(AuthConfigurationError):
        get_auth_settings()


@pytest.mark.parametrize(
    "changes",
    [
        {"max_failures": 0},
        {"max_failures": True},
        {"max_anonymous_sessions": 0},
        {"session_lifetime": timedelta(0)},
        {"session_lifetime": timedelta(days=1)},
        {"anonymous_lifetime": timedelta(days=1)},
        {"failure_window": timedelta(days=1)},
        {"cookie_secure": "false"},
    ],
)
def test_auth_limits_are_bounded(settings: AuthSettings, changes: dict[str, object]) -> None:
    with pytest.raises(AuthConfigurationError):
        replace(settings, **changes)


def test_issued_tokens_are_random_and_only_digests_match() -> None:
    first, second = issue_token(), issue_token()
    assert first != second
    digest = token_digest(first)
    assert digest is not None and len(digest) == 64 and first not in digest
    assert matches_token_hash(first, digest)
    assert not matches_token_hash(second, digest)


@pytest.mark.parametrize("value", [None, "", "short", "é" * 43, "x" * 44, "x" * 42 + "/"])
def test_malformed_credentials_do_not_hash(value: str | None) -> None:
    assert token_digest(value) is None
    assert not matches_token_hash(value, "a" * 64)


def test_session_cookies_are_private_and_expiring(settings: AuthSettings) -> None:
    credentials = SessionCredentials(issue_token(), issue_token(), datetime.now(UTC))
    response = Response()
    set_session_cookies(response, credentials, settings)
    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 3
    assert cookies[0].startswith(SESSION_COOKIE + "=") and "Max-Age=0" in cookies[0]
    assert "Path=/ui" in cookies[0]
    assert cookies[1].startswith(SESSION_COOKIE + "=") and "Path=/;" in cookies[1]
    assert cookies[2].startswith(CSRF_COOKIE + "=") and "Path=/ui" in cookies[2]
    for cookie in cookies:
        assert "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=strict" in cookie
        assert "expires=" in cookie
    assert credentials.token not in repr(credentials)
    assert credentials.csrf_token not in repr(credentials)


def test_logout_removes_both_cookies(settings: AuthSettings) -> None:
    response = Response()
    clear_session_cookies(response, settings)
    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 3 and "Path=/;" in cookies[0]
    assert all("Path=/ui" in value for value in cookies[1:])
    for cookie in cookies:
        assert "Max-Age=0" in cookie
        assert "HttpOnly" in cookie and "Secure" in cookie


def test_test_http_cookie_override_is_explicit(settings: AuthSettings) -> None:
    response = Response()
    credentials = SessionCredentials(issue_token(), issue_token(), datetime.now(UTC))
    set_session_cookies(response, credentials, replace(settings, cookie_secure=False))
    assert all("Secure" not in value for value in response.headers.getlist("set-cookie"))


def test_client_fingerprint_hides_address_and_separates_clients() -> None:
    first = client_fingerprint("synthetic-client-a")
    second = client_fingerprint("synthetic-client-b")
    assert len(first) == 64 and first != second
    assert "synthetic-client-a" not in first
    assert client_fingerprint("synthetic-client-a") == first
    assert client_fingerprint(None) == client_fingerprint(None)


def test_forwarded_header_does_not_change_client_identity() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ui/login",
        "query_string": b"",
        "headers": [
            (b"x-forwarded-for", b"synthetic-client-b"),
            (b"forwarded", b"for=synthetic-client-b"),
            (b"x-real-ip", b"synthetic-client-b"),
        ],
        "client": ("synthetic-client-a", 4242),
    }
    assert client_address(Request(scope)) == "synthetic-client-a"
    assert client_address(Request({**scope, "client": None})) is None
    assert client_fingerprint(client_address(Request(scope))) != client_fingerprint(
        "synthetic-client-b"
    )
