"""Owner password configuration and opaque UI authentication primitives."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.responses import Response

SESSION_COOKIE = "deck_session"
CSRF_COOKIE = "deck_csrf"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_MAX_PASSWORD_BYTES = 1024
_PASSWORD_HASHER = PasswordHasher()


class AuthConfigurationError(ValueError):
    """Fail closed without including a configured value in an error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Owner login configuration is unavailable.")


@dataclass(frozen=True)
class AuthSettings:
    """One owner account, secure cookies, and bounded session/login lifetimes."""

    password_hash: str = field(repr=False)
    cookie_secure: bool = True
    session_lifetime: timedelta = timedelta(hours=12)
    idle_lifetime: timedelta = timedelta(minutes=30)
    anonymous_lifetime: timedelta = timedelta(minutes=10)
    failure_window: timedelta = timedelta(minutes=15)
    max_failures: int = 5
    max_anonymous_sessions: int = 1000
    daily_failure_window: timedelta = timedelta(hours=24)
    max_daily_failures: int = 20

    def __post_init__(self) -> None:
        _validate_password_hash(self.password_hash)
        if (
            type(self.cookie_secure) is not bool
            or not timedelta(0) < self.session_lifetime <= timedelta(hours=12)
            or not timedelta(0) < self.idle_lifetime <= timedelta(hours=12)
            or not timedelta(0) < self.anonymous_lifetime <= timedelta(hours=1)
            or not timedelta(0) < self.failure_window <= timedelta(hours=1)
            or type(self.max_failures) is not int
            or not 1 <= self.max_failures <= 20
            or type(self.max_anonymous_sessions) is not int
            or not 1 <= self.max_anonymous_sessions <= 10000
            or not timedelta(0) < self.daily_failure_window <= timedelta(hours=24)
            or type(self.max_daily_failures) is not int
            or not 1 <= self.max_daily_failures <= 100
        ):
            raise AuthConfigurationError("invalid_auth_limits")


@dataclass(frozen=True)
class SessionCredentials:
    """Raw browser credentials are returned once and never stored in the database."""

    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


def _validate_password_hash(value: str) -> None:
    if not value:
        raise AuthConfigurationError("owner_password_unconfigured")
    try:
        if len(value) > 512:
            raise ValueError
        parameters = extract_parameters(value)
        if (
            parameters.type != Type.ID
            or parameters.version != 19
            or not 19456 <= parameters.memory_cost <= 262144
            or not 2 <= parameters.time_cost <= 10
            or not 1 <= parameters.parallelism <= 8
            or parameters.salt_len < 16
            or not 16 <= parameters.hash_len <= 64
        ):
            raise ValueError
    except (InvalidHashError, ValueError, TypeError):
        raise AuthConfigurationError("owner_password_invalid") from None


def get_auth_settings() -> AuthSettings:
    """Load only an Argon2id hash; HTTP test cookies require an explicit opt-out."""
    cookie_secure = os.environ.get("DECK_UI_COOKIE_SECURE", "true").lower()
    if cookie_secure not in {"true", "false"}:
        raise AuthConfigurationError("invalid_cookie_security")
    return AuthSettings(
        password_hash=os.environ.get("DECK_OWNER_PASSWORD_HASH", ""),
        cookie_secure=cookie_secure == "true",
    )


def verify_owner_password(password: str, settings: AuthSettings) -> bool:
    """Verify bounded input without logging the password or exposing hash details."""
    try:
        if not password or len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
            return False
        return _PASSWORD_HASHER.verify(settings.password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeError):
        return False


def password_fingerprint(settings: AuthSettings) -> str:
    """Bind existing sessions to the current password hash without storing that hash."""
    return hashlib.sha256(settings.password_hash.encode()).hexdigest()


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str | None) -> str | None:
    """Reject malformed credentials before hashing or querying a session."""
    if token is None or _TOKEN_PATTERN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def matches_token_hash(provided: str | None, expected: str) -> bool:
    digest = token_digest(provided)
    return digest is not None and hmac.compare_digest(digest, expected)


def set_session_cookies(
    response: Response, credentials: SessionCredentials, settings: AuthSettings
) -> None:
    """Keep both opaque values out of JavaScript and limit them to UI requests."""
    for name, value in (
        (SESSION_COOKIE, credentials.token),
        (CSRF_COOKIE, credentials.csrf_token),
    ):
        response.set_cookie(
            name,
            value,
            expires=credentials.expires_at.astimezone(UTC),
            path="/ui",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )


def clear_session_cookies(response: Response, settings: AuthSettings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/ui",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
