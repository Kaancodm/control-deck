"""Static UI protections and body limits require no database connection."""

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.ui.middleware import MAX_BODY_BYTES


def test_static_assets_are_local_and_protected():
    with TestClient(create_app()) as client:
        response = client.get("/ui/static/app.css")
    assert response.status_code == 200
    assert "--color-bg:" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/ui/login", "/ui/tasks/new", "/ui/chat"])
def test_oversized_body_is_rejected_before_auth_or_parser(path):
    with TestClient(create_app()) as client:
        response = client.post(path, content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
