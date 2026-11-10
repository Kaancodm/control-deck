"""The root path redirects to the UI without reading a database, cookie or query."""

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.security import SECURITY_HEADERS
from app.ui import middleware

# Pinned effective policies (plan finding F5). A change here is a security decision.
BOUNDARY_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
INNER_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@pytest.fixture
def client(monkeypatch):
    # Any database access in these checks would fail loudly.
    monkeypatch.setattr("app.api.routes.health.get_session_factory", lambda: pytest.fail("No DB"))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/", "/?next=https://example.invalid", "/?return_to=//x"])
def test_root_redirects_to_ui_with_a_fixed_target(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui"
    assert response.headers["content-security-policy"] == BOUNDARY_CSP
    assert response.headers["cache-control"] == "no-store"


def test_ui_index_redirects_to_the_overview(client):
    response = client.get("/ui", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/ui/overview"


def test_root_accepts_only_get(client):
    assert client.post("/", follow_redirects=False).status_code == 405


def test_health_paths_are_unchanged(client):
    assert client.get("/healthz").status_code == 200
    response = client.get("/api/v1/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_openapi_does_not_list_ui_or_root(client):
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/" not in paths
    assert not [path for path in paths if path.startswith("/ui")]


def test_csp_constants_are_pinned():
    assert SECURITY_HEADERS["Content-Security-Policy"] == BOUNDARY_CSP
    assert middleware._HEADERS["content-security-policy"] == INNER_CSP


def test_cross_site_requests_to_root_stay_forbidden(client):
    response = client.get("/", headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
    assert response.status_code == 403
