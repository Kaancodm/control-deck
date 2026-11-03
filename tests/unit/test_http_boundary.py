"""HTTP boundaries reject invalid inputs before application side effects."""

from __future__ import annotations

import asyncio

import pytest

from app.api.security import SECURITY_HEADERS, HttpBoundaryMiddleware, RequestLimits


def exercise(
    *, headers=None, chunks=None, limits=None, scheme="http", delayed=False, path="/api/v1/tasks"
):
    seen = []
    sent = []
    source = list(chunks or [{"type": "http.request", "body": b"", "more_body": False}])

    async def app(scope, receive, send):
        seen.append((scope, await receive()))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"public")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        if delayed:
            await asyncio.sleep(0.05)
        return source.pop(0)

    async def send(message):
        sent.append(message)

    boundary = HttpBoundaryMiddleware(
        app, limits=limits or RequestLimits(("testserver",), max_body_bytes=16)
    )
    asyncio.run(
        boundary(
            {
                "type": "http",
                "method": "POST",
                "scheme": scheme,
                "headers": [(b"host", b"testserver"), *(headers or [])],
                "path": path,
            },
            receive,
            send,
        )
    )
    return seen, sent


@pytest.mark.parametrize(
    "headers",
    [
        [(b"host", b"other")],
        [(b"content-length", b"3"), (b"content-length", b"3")],
        [(b"content-length", b"3"), (b"transfer-encoding", b"chunked")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"1,1")],
        [(b"transfer-encoding", b"gzip")],
    ],
)
def test_ambiguous_headers_never_reach_application(headers) -> None:
    seen, sent = exercise(headers=headers)
    assert not seen
    assert sent[0]["status"] == 400


def test_chunked_body_enforces_actual_size_before_dispatch() -> None:
    seen, sent = exercise(
        headers=[(b"transfer-encoding", b"chunked")],
        chunks=[
            {"type": "http.request", "body": b"a" * 10, "more_body": True},
            {"type": "http.request", "body": b"b" * 7, "more_body": False},
        ],
    )
    assert not seen
    assert sent[0]["status"] == 413


def test_declared_size_and_mismatched_short_length_rejected() -> None:
    seen, sent = exercise(headers=[(b"content-length", b"17")])
    assert not seen and sent[0]["status"] == 413
    seen, sent = exercise(
        headers=[(b"content-length", b"1")], chunks=[{"type": "http.request", "body": b"ab"}]
    )
    assert not seen and sent[0]["status"] == 400


def test_valid_chunks_are_replayed_exactly_once() -> None:
    seen, sent = exercise(
        headers=[(b"transfer-encoding", b"chunked")],
        chunks=[
            {"type": "http.request", "body": b"a\x00", "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ],
    )
    assert seen[0][1]["body"] == b"a\x00b"
    assert sent[0]["status"] == 200
    actual = dict(sent[0]["headers"])
    for name, value in SECURITY_HEADERS.items():
        assert actual[name.lower().encode()] == value.encode()


def test_body_timeout_and_disconnect_never_dispatch() -> None:
    seen, sent = exercise(
        limits=RequestLimits(("testserver",), body_timeout_seconds=0.001), delayed=True
    )
    assert not seen and sent[0]["status"] == 408
    seen, sent = exercise(chunks=[{"type": "http.disconnect"}])
    assert not seen and sent[0]["status"] == 400


@pytest.mark.parametrize(
    "origin",
    [
        b"null",
        b"https://testserver",
        b"http://foreign.invalid",
        b"http://testserver/path",
        b"http://user@testserver",
        b"http://testserver:9999",
    ],
)
def test_cross_origin_rejected_without_reflecting_values(origin: bytes) -> None:
    seen, sent = exercise(headers=[(b"origin", origin)])
    assert not seen and sent[0]["status"] == 403
    assert origin not in sent[1]["body"]


def test_same_origin_accepts_default_port_and_discards_forwarding_headers() -> None:
    seen, sent = exercise(
        headers=[
            (b"origin", b"http://testserver:80"),
            (b"x-forwarded-host", b"foreign.invalid"),
            (b"x-forwarded-proto", b"https"),
            (b"forwarded", b"for=synthetic"),
        ]
    )
    assert sent[0]["status"] == 200
    assert dict(seen[0][0]["headers"]) == {
        b"host": b"testserver",
        b"origin": b"http://testserver:80",
    }


def test_cross_site_fetch_rejected_even_without_origin() -> None:
    seen, sent = exercise(headers=[(b"sec-fetch-site", b"cross-site")])
    assert not seen and sent[0]["status"] == 403


def test_header_limit_covers_all_names_and_values() -> None:
    seen, sent = exercise(
        headers=[(b"x-large", b"a" * 200)],
        limits=RequestLimits(("testserver",), max_header_bytes=100),
    )
    assert not seen and sent[0]["status"] == 431


@pytest.mark.parametrize(
    "name", [b"authorization", b"x-deck-worker-token", b"x-csrf-token", b"sec-fetch-site"]
)
def test_duplicate_security_headers_rejected(name: bytes) -> None:
    seen, sent = exercise(headers=[(name, b"synthetic"), (name, b"synthetic")])
    assert not seen and sent[0]["status"] == 400


def test_opaque_same_origin_ui_form_reaches_csrf_handler_only() -> None:
    headers = [(b"origin", b"null"), (b"sec-fetch-site", b"same-origin")]
    seen, sent = exercise(headers=headers, path="/ui/login")
    assert seen and sent[0]["status"] == 200
    seen, sent = exercise(headers=headers)
    assert not seen and sent[0]["status"] == 403
    seen, sent = exercise(headers=[(b"origin", b"null")], path="/ui/login")
    assert not seen and sent[0]["status"] == 403


def test_explicit_proxy_origin_is_configuration_not_forwarded_header() -> None:
    limits = RequestLimits(("testserver",), public_origin="https://testserver")
    seen, sent = exercise(headers=[(b"origin", b"https://testserver")], limits=limits)
    assert seen and sent[0]["status"] == 200
    seen, sent = exercise(
        headers=[(b"origin", b"http://testserver"), (b"x-forwarded-proto", b"http")], limits=limits
    )
    assert not seen and sent[0]["status"] == 403
