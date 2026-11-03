"""Unit tests for RFC 7807 problem responses (no database)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app


def test_healthz_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_without_database_url_is_problem_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECK_DATABASE_URL", raising=False)
    monkeypatch.delenv("DECK_TEST_DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["title"] == "Not ready"
    assert body["status"] == 503
