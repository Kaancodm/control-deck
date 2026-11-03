"""Bound the complete HTTP request before dispatching any application side effect."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.bind import DeploymentConfigurationError, normalize_host

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass(frozen=True)
class RequestLimits:
    allowed_hosts: tuple[str, ...]
    max_body_bytes: int = 6 * 1024 * 1024
    body_timeout_seconds: float = 10
    max_header_bytes: int = 16384
    max_headers: int = 100

    def __post_init__(self) -> None:
        if (
            not self.allowed_hosts
            or len(self.allowed_hosts) > 16
            or any(normalize_host(host) != host for host in self.allowed_hosts)
            or type(self.max_body_bytes) is not int
            or not 1 <= self.max_body_bytes <= 8 * 1024 * 1024
            or not 0 < self.body_timeout_seconds <= 30
            or not 1 <= self.max_header_bytes <= 32768
            or not 1 <= self.max_headers <= 200
        ):
            raise DeploymentConfigurationError("invalid_request_limits")


def _authority(value: bytes) -> tuple[str, int | None]:
    try:
        text = value.decode("ascii")
        if not text or any(char in text for char in " /\\@?#,%"):
            raise ValueError
        parsed = urlsplit("//" + text)
        if parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError
        host = normalize_host(parsed.hostname)
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        return host, port
    except (ValueError, UnicodeError):
        raise DeploymentConfigurationError("invalid_host_header") from None


def _same_origin(value: bytes, host: tuple[str, int | None], scheme: str) -> bool:
    try:
        origin = urlsplit(value.decode("ascii"))
        if (
            origin.scheme != scheme
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username is not None
            or origin.password is not None
        ):
            return False
        expected_port = host[1] or (443 if scheme == "https" else 80)
        actual = _authority(origin.netloc.encode("ascii"))
        actual_port = actual[1] or (443 if scheme == "https" else 80)
        return actual[0] == host[0] and actual_port == expected_port
    except (ValueError, UnicodeError):
        return False


class HttpBoundaryMiddleware:
    """Do not trust forwarded headers or start handlers before a bounded body is complete."""

    def __init__(self, app: ASGIApp, *, limits: RequestLimits) -> None:
        self.app = app
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers", []))
        if (
            len(headers) > self.limits.max_headers
            or sum(len(k) + len(v) for k, v in headers) > self.limits.max_header_bytes
        ):
            await self._reject(send, 431, "request_headers_too_large")
            return
        values: dict[bytes, list[bytes]] = {}
        for key, value in headers:
            values.setdefault(key.lower(), []).append(value)
        try:
            hosts = values.get(b"host", [])
            if len(hosts) != 1:
                raise ValueError
            host = _authority(hosts[0])
            if host[0] not in self.limits.allowed_hosts:
                raise ValueError
        except (ValueError, DeploymentConfigurationError):
            await self._reject(send, 400, "invalid_host")
            return
        origins = values.get(b"origin", [])
        if len(origins) > 1 or (
            origins and not _same_origin(origins[0], host, scope.get("scheme", "http"))
        ):
            await self._reject(send, 403, "invalid_origin")
            return
        if values.get(b"sec-fetch-site", []) == [b"cross-site"]:
            await self._reject(send, 403, "cross_site_request")
            return
        lengths = values.get(b"content-length", [])
        transfers = values.get(b"transfer-encoding", [])
        if len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers):
            await self._reject(send, 400, "ambiguous_body_length")
            return
        expected: int | None = None
        if lengths:
            raw = lengths[0]
            if len(raw) > 20 or not raw.isdigit():
                await self._reject(send, 400, "invalid_body_length")
                return
            expected = int(raw)
            if expected > self.limits.max_body_bytes:
                await self._reject(send, 413, "request_body_too_large")
                return
        if transfers and transfers != [b"chunked"]:
            await self._reject(send, 400, "invalid_transfer_encoding")
            return
        body = bytearray()
        try:
            async with asyncio.timeout(self.limits.body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] != "http.request":
                        await self._reject(send, 400, "incomplete_request_body")
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.limits.max_body_bytes:
                        await self._reject(send, 413, "request_body_too_large")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._reject(send, 408, "request_body_timeout")
            return
        if expected is not None and len(body) != expected:
            await self._reject(send, 400, "body_length_mismatch")
            return
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                protected = {name.lower().encode() for name in SECURITY_HEADERS}
                filtered = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in protected
                ]
                message = {**message, "headers": [*filtered, *_security_headers()]}
            await send(message)

        # Forwarding metadata is never an authentication or origin authority.
        safe_scope = {
            **scope,
            "headers": [
                (key, value)
                for key, value in headers
                if key.lower()
                not in {b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"}
            ],
        }
        await self.app(safe_scope, replay, secure_send)

    @staticmethod
    async def _reject(send: Send, status: int, code: str) -> None:
        body = json.dumps(
            {"type": "about:blank", "title": "Request rejected", "status": status, "detail": code}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                    *_security_headers(),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _security_headers() -> list[tuple[bytes, bytes]]:
    return [(name.lower().encode(), value.encode()) for name, value in SECURITY_HEADERS.items()]
