"""Protected local health, paginated audit and next-response authentication warnings."""

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.audit.service import read_lane_health
from app.core.schemas import Worker
from app.db.models import Event, LaneStatus, Setting
from app.db.ui_auth import UiSession
from app.operations.models import OperationEvent
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE
from app.workers.lane_status import LaneHealthState, update_lane_status
from tests.integration.test_api import api_client as api_client
from tests.integration.test_api import owner_token as owner_token
from tests.integration.test_api import worker_token as worker_token
from tests.integration.test_attempt_health import running, submit
from tests.integration.test_database import _insert_task
from tests.integration.test_ui_flows import login
from tests.integration.test_ui_flows import password_hash as password_hash
from tests.integration.test_ui_flows import ui_client as ui_client


@pytest.fixture
def health_client(ui_client, db_session, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    monkeypatch.setattr("app.api.routes.health.get_session_factory", lambda: factory)
    monkeypatch.setenv("DECK_OWNER_TOKEN", "synthetic-health-owner")
    monkeypatch.setenv("DECK_WORKER_TOKEN_OLLAMA", "synthetic-health-worker")
    return ui_client


def test_anonymous_health_is_minimal_and_does_not_open_database(monkeypatch):
    monkeypatch.setattr("app.api.routes.health.get_session_factory", lambda: pytest.fail("No DB"))
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_owner_bearer_health_keeps_simulation_and_quota_explicit(health_client, db_session):
    update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    db_session.commit()
    response = health_client.get(
        "/api/v1/health", headers={"Authorization": "Bearer synthetic-health-owner"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["db"] == {"connected": True, "schema": "current"}
    assert data["counter_scope"] == "since_migration_0016"
    assert data["ollama"]["execution_mode"] == "simulation"
    assert data["ollama"]["last_attempt_success_at"] is None
    assert all(
        lane["provider_quota"] == "unknown" and lane["live_available"] is False
        for lane in data["lanes"]
    )


def test_lane_view_distinguishes_unclassified_failure_from_no_error(health_client, db_session):
    update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    row = db_session.get(LaneStatus, Worker.OLLAMA.value)
    row.completed_attempts = row.consecutive_failures = 1
    row.last_attempt_at = row.last_error_at = datetime.now(UTC)
    db_session.commit()
    login(health_client)
    response = health_client.get("/ui/lanes")
    assert response.status_code == 200
    assert "Letzter Fehler: unbekannt" in response.text
    assert "Letzter Fehler: keiner" in response.text  # Lanes without any recorded error.


def test_session_cookie_reaches_health_without_granting_write_api_access(health_client):
    csrf = login(health_client)
    request = health_client.build_request("GET", "/api/v1/health")
    assert SESSION_COOKIE in request.headers["cookie"]
    assert CSRF_COOKIE not in request.headers["cookie"]
    assert health_client.get("/api/v1/health").status_code == 200
    assert (
        health_client.patch(
            "/api/v1/control/settings", json={"changes": {"paused": True}}
        ).status_code
        == 401
    )
    assert (
        health_client.post("/ui/logout", data={"csrf": csrf}, follow_redirects=False).status_code
        == 303
    )
    assert SESSION_COOKIE not in health_client.cookies
    assert health_client.get("/api/v1/health").json() == {"status": "ok"}


@pytest.mark.parametrize("mode", ["expired", "idle", "revoked", "password"])
def test_invalidated_session_cannot_read_health(health_client, db_session, mode):
    login(health_client)
    row = db_session.scalar(select(UiSession).where(UiSession.authenticated.is_(True)))
    if mode == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif mode == "idle":
        row.last_active_at = datetime.now(UTC) - timedelta(minutes=31)
    elif mode == "revoked":
        row.revoked_at = datetime.now(UTC)
    else:
        row.password_fingerprint = "0" * 64
    db_session.commit()
    response = health_client.get("/api/v1/health")
    assert response.status_code == 401
    assert "lanes" not in response.text


@pytest.mark.parametrize(
    "authorization", ["Bearer synthetic-health-worker", "Basic invalid", "Bearer wrong"]
)
def test_bad_bearer_never_falls_back_to_valid_session(health_client, authorization):
    login(health_client)
    assert (
        health_client.get("/api/v1/health", headers={"Authorization": authorization}).status_code
        == 401
    )


def test_health_errors_are_redacted(health_client, monkeypatch):
    def unavailable():
        raise SQLAlchemyError("private-health-marker")

    monkeypatch.setattr("app.api.routes.health.get_session_factory", unavailable)
    response = health_client.get(
        "/api/v1/health", headers={"Authorization": "Bearer synthetic-health-owner"}
    )
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_invalid_runner_controls_fail_as_safe_unavailable_health(health_client, db_session):
    db_session.add(Setting(key="private-invalid-control", value=True))
    db_session.commit()
    response = health_client.get(
        "/api/v1/health", headers={"Authorization": "Bearer synthetic-health-owner"}
    )
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_outdated_schema_prevents_lane_projection(health_client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.health.read_lane_health", lambda _session: pytest.fail("No lane query")
    )
    db_session.execute(text("UPDATE alembic_version SET version_num='20251103_0015'"))
    db_session.commit()
    try:
        response = health_client.get(
            "/api/v1/health", headers={"Authorization": "Bearer synthetic-health-owner"}
        )
        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "db": {"connected": True, "schema": "outdated"},
        }
    finally:
        db_session.execute(text("UPDATE alembic_version SET version_num='20251103_0016'"))
        db_session.commit()


def test_health_read_does_not_touch_activity_or_queue(health_client, db_session):
    login(health_client)
    row = db_session.scalar(select(UiSession).where(UiSession.authenticated.is_(True)))
    before = row.last_active_at
    db_session.commit()
    assert health_client.get("/api/v1/health").status_code == 200
    db_session.refresh(row)
    assert row.last_active_at == before
    statements = []
    connection = db_session.connection()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.upper())

    event.listen(connection, "before_cursor_execute", capture)
    try:
        assert len(read_lane_health(db_session)) == 3
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert all(
        item.lstrip().startswith("SELECT") and "FOR UPDATE" not in item for item in statements
    )


def test_auth_required_result_appears_on_next_ui_response(api_client, ui_client, db_session):
    login(ui_client)
    envelope = running(api_client)
    assert submit(api_client, envelope, error="auth_required").status_code == 200
    response = ui_client.get("/ui/tasks")
    assert response.status_code == 200
    assert 'data-lane-warning="auth_required"' in response.text
    assert "Anmeldung erforderlich" in response.text and "ollama" in response.text
    row = db_session.get(LaneStatus, "ollama")
    assert row.completed_attempts == row.consecutive_failures == 1
    assert row.last_error_code == "auth_required"
    assert 'data-lane-warning="auth_required"' in ui_client.get("/ui/fragments/status").text


def test_recent_preflight_does_not_hide_old_claim_warning(ui_client, db_session):
    login(ui_client)
    task = _insert_task(db_session)
    db_session.add(
        Event(
            task_id=task.id,
            event_type="task.acquire",
            actor="runner",
            attempt=1,
            to_status=task.status,
            detail={"lane": "ollama"},
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
    )
    update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    db_session.commit()
    response = ui_client.get("/ui/lanes")
    assert 'data-lane-warning="no_recent_success"' in response.text
    assert "Anbieter-Kontingent: unbekannt" in response.text


@pytest.mark.parametrize("path", ["/ui/audit", "/ui/audit.csv", "/ui/lanes"])
def test_audit_and_health_pages_require_owner(ui_client, path):
    response = ui_client.get(path, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/ui/login"


def test_audit_csv_matches_filtered_visible_page_and_excludes_raw_data(ui_client, db_session):
    login(ui_client)
    for index in range(3):
        db_session.add(
            OperationEvent(
                operation_id=uuid4(),
                operation_type="backup",
                phase="completed",
                detail={"rows": index, "path": "private-audit-marker", "reason": "=unsafe"},
            )
        )
    db_session.commit()
    query = "?source=operation&event_type=operation.backup&limit=2&offset=1"
    html = ui_client.get("/ui/audit" + query)
    response = ui_client.get("/ui/audit.csv" + query)
    assert html.status_code == response.status_code == 200
    assert "3 Ereignisse" in html.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert all(row["id"] in html.text and row["event_type"] == "operation.backup" for row in rows)
    assert "private-audit-marker" not in html.text + response.text
    assert "=unsafe" not in html.text + response.text
    assert "attachment" in response.headers["content-disposition"]
    assert "auth.login_succeeded" in ui_client.get("/ui/audit?source=auth").text


@pytest.mark.parametrize(
    "query",
    [
        "?limit=201",
        "?source=private",
        "?offset=-1",
        "?lane=ollama&lane=claude",
        "?since=2026-01-01",
        "?task_id=bad",
    ],
)
def test_bad_audit_filter_is_rejected(ui_client, query):
    login(ui_client)
    assert ui_client.get("/ui/audit" + query).status_code == 422
    assert ui_client.get("/ui/audit.csv" + query).status_code == 422
