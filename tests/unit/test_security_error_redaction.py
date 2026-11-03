"""HTTP errors must not expose database configuration or fail on unusual credentials."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.api.deps import require_owner
from app.api.routes import health
from app.db.config import DatabaseConfigurationError


def test_non_ascii_owner_token_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_OWNER_TOKEN", "synthetic-owner")
    for worker in ("OLLAMA", "CLAUDE", "CODEX"):
        monkeypatch.delenv(f"DECK_WORKER_TOKEN_{worker}", raising=False)
    with pytest.raises(HTTPException) as caught:
        require_owner(authorization="Bearer ⛄")
    assert caught.value.status_code == 401


def test_readiness_configuration_failure_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail():
        raise DatabaseConfigurationError("synthetic-private-database-location")

    monkeypatch.setattr(health, "get_database_url", fail)
    response = health.readyz()
    assert response.status_code == 503
    assert b"synthetic-private" not in response.body


def test_readiness_driver_failure_is_redacted_and_engine_disposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = []

    class Engine:
        def connect(self):
            raise OperationalError(
                "synthetic-private-query", {}, Exception("synthetic-private-driver")
            )

        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(health, "get_database_url", lambda: "synthetic-unused")
    monkeypatch.setattr(health, "get_engine_kwargs", dict)
    monkeypatch.setattr(health, "create_engine", lambda *args, **kwargs: Engine())
    response = health.readyz()
    assert response.status_code == 503
    assert b"synthetic-private" not in response.body
    assert disposed == [True]


def test_unhandled_http_failure_is_generic_and_has_security_headers() -> None:
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()

    @app.get("/synthetic-failure")
    def fail():
        raise RuntimeError("synthetic-private-exception-value")

    response = TestClient(app).get("/synthetic-failure")
    assert response.status_code == 500
    assert "synthetic-private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_application_refuses_missing_bind_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from app.core.bind import DeploymentConfigurationError

    monkeypatch.delenv("DECK_BIND_HOST")
    with pytest.raises(DeploymentConfigurationError):
        with TestClient(create_app()):
            pass
