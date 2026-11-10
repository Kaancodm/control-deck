"""Owner overview against PostgreSQL: auth, real counts, stored events, flag states only."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.state_machine import TaskState
from app.operations.models import OperationEvent
from app.services.tasks import act_on_task
from tests.integration.test_database import _insert_task
from tests.integration.test_ui_flows import PASSWORD, Inputs, login
from tests.integration.test_ui_flows import password_hash as password_hash
from tests.integration.test_ui_flows import ui_client as ui_client
from tests.unit.test_ui_root import BOUNDARY_CSP

SENTINELS = {
    "DECK_ROUTINE_WAKE_URL": "https://sentinel-wake-7411.example.invalid/hook",
    "DECK_ROUTINE_WAKE_AUTHORIZATION": "Bearer sentinel-wake-key-6203",
    "DECK_OLLAMA_URL": "http://127.0.0.1:59999/sentinel-ollama",
    "DECK_ASSISTANT_OLLAMA_MODEL": "sentinel-model:tag",
    "DECK_ASSISTANT_SEARXNG_URL": "https://sentinel-search.example.invalid/",
    "DECK_ASSISTANT_MEDIAWIKI_API": "https://sentinel-wiki.example.invalid/w/api.php",
    "DECK_ASSISTANT_WEB_ALLOWLIST": "sentinel-allow.example.invalid",
    "DECK_OWNER_TOKEN": "sentinel-owner-token-5521",
}


def test_overview_requires_an_owner_session(ui_client):
    response = ui_client.get("/ui/overview", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/ui/login"
    # Anonymous chain: / -> /ui -> /ui/overview -> /ui/login.
    final = ui_client.get("/")
    assert final.status_code == 200 and str(final.url).endswith("/ui/login")


def test_login_lands_on_the_overview_and_ignores_next(ui_client):
    page = ui_client.get("/ui/login")
    csrf = Inputs(page.text).inputs["csrf"]
    response = ui_client.post(
        "/ui/login",
        data={"csrf": csrf, "password": PASSWORD, "next": "https://example.invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/ui/overview"
    again = ui_client.get("/ui/login", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == "/ui/overview"
    final = ui_client.get("/")
    assert final.status_code == 200 and str(final.url).endswith("/ui/overview")
    assert "Owner-Konsole" in final.text


def test_overview_counts_real_approvals_lanes_and_events(ui_client, db_session):
    login(ui_client)
    first = _insert_task(db_session, approved=False)
    _insert_task(db_session, approved=False)
    db_session.add(
        OperationEvent(
            operation_id=uuid4(),
            operation_type="backup",
            phase="completed",
            detail={"rows": 3, "output": "private-overview-marker"},
        )
    )
    db_session.commit()
    assert first.status == TaskState.AWAITING_APPROVAL
    html = ui_client.get("/ui/overview").text
    assert "2 GO offen" in html
    assert '<p class="kpi-value">2</p>' in html
    assert 'data-open-point="approvals"' in html and "2 Freigaben offen" in html
    for lane in ("ollama", "claude", "codex"):
        assert f"<b>{lane}</b>" in html
    assert "3 Lanes" in html
    assert 'class="audit-log"' in html and "task." in html
    assert "abgeschlossen" in html and "private-overview-marker" not in html
    assert 'aria-current="step"' in html  # the focus task highlights its own step
    assert 'href="/ui/audit.csv?limit=5"' in html
    act_on_task(db_session, first.id, "approve")
    db_session.commit()
    html = ui_client.get("/ui/overview").text
    assert "1 GO offen" in html and '<p class="kpi-value">1</p>' in html


@pytest.mark.parametrize("path", ["/ui/login", "/ui/overview", "/ui/assistant"])
def test_real_responses_carry_exactly_the_boundary_csp(ui_client, path):
    if path != "/ui/login":
        login(ui_client)
    response = ui_client.get(path)
    assert response.status_code == 200
    assert response.headers.get_list("content-security-policy") == [BOUNDARY_CSP]


def test_configured_values_never_reach_the_html(ui_client, monkeypatch):
    for name, value in SENTINELS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DECK_ASSISTANT_OLLAMA", "1")
    monkeypatch.setenv("DECK_ASSISTANT_WEB", "1")
    monkeypatch.setenv("DECK_ASSISTANT_SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("DECK_ROUTINE_WAKE", "1")
    login(ui_client)
    pages = {path: ui_client.get(path).text for path in ("/ui/overview", "/ui/assistant")}
    for path, html in pages.items():
        assert "sentinel" not in html, path
        for value in SENTINELS.values():
            assert value not in html, path
        for number in ("7411", "6203", "5521", "59999"):
            assert number not in html, (path, number)
    overview = pages["/ui/overview"]
    assert '<span class="chip chip--on">konfiguriert</span>' in overview
    assert overview.count('<span class="chip chip--on">AN</span>') >= 4
    assert "Routine-Wake: an" in pages["/ui/assistant"]
    monkeypatch.setenv("DECK_ROUTINE_WAKE_URL", "http://sentinel-plain.example.invalid/")
    overview = ui_client.get("/ui/overview").text
    assert '<span class="chip chip--warn">unvollständig</span>' in overview
    assert 'data-open-point="routine_wake"' in overview and "Sender unvollständig" in overview
    assert "sentinel" not in overview
