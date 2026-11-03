"""Static UI protections and body limits require no database connection."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.ui.middleware import MAX_BODY_BYTES, UiSecurityMiddleware


def test_static_assets_are_local_and_protected():
    with TestClient(create_app()) as client:
        response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    assert "--color-bg:" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/ui", 303),
        ("GET", "/ui/static/app.css", 200),
        ("HEAD", "/ui/static/vendor/htmx.min.js", 200),
        ("GET", "/ui/static/missing.css", 404),
        ("GET", "/ui/missing", 404),
    ],
)
def test_security_headers_cover_assets_redirects_and_ui_errors(method, path, status):
    with TestClient(create_app()) as client:
        response = client.request(method, path, follow_redirects=False)
    assert response.status_code == status
    csp = response.headers["content-security-policy"]
    # Stage 15 hardens the fallback to default-src 'none', which also covers object-src.
    assert "default-src 'none'" in csp or (
        "default-src 'self'" in csp and "object-src 'none'" in csp
    )
    for directive in (
        "script-src 'self'",
        "style-src 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/ui/login", "/ui/tasks/new", "/ui/chat"])
def test_oversized_body_is_rejected_before_auth_or_parser(path):
    with TestClient(create_app()) as client:
        response = client.post(path, content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("status", [200, 303, 403, 404, 413])
def test_middleware_secures_success_redirect_and_error_responses(status):
    messages = []
    invoked = []

    async def app(scope, receive, send):
        invoked.append(True)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-security-policy", b"default-src *"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"x" * (MAX_BODY_BYTES + 1), "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(
        UiSecurityMiddleware(app)(
            {"type": "http", "path": "/ui/test", "method": "POST" if status == 413 else "GET"},
            receive,
            send,
        )
    )
    start = messages[0]
    assert start["status"] == status
    headers = dict(start["headers"])
    assert "frame-ancestors 'none'" in headers[b"content-security-policy"].decode()
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert len([key for key, _ in start["headers"] if key == b"content-security-policy"]) == 1
    assert bool(invoked) == (status != 413)
