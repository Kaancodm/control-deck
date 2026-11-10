"""Owner overview against PostgreSQL: auth, real counts, stored events, flag states only."""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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
    # Separate transactions give separate now() values, so "oldest" is well defined.
    db_session.commit()
    second = _insert_task(db_session, approved=False)
    db_session.commit()
    assert first.created_at < second.created_at
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
    # Owner decision: agy is the fourth lane, shown without data and not connected yet.
    assert '<p class="kpi-value">4</p>' in html and "3 im Deck angebunden" in html
    assert "4 Lanes" in html and 'data-lane="agy"' in html
    assert "eigene Lane · im Deck noch nicht angebunden" in html
    assert 'data-open-point="lane_agy_pending"' in html
    assert "KEINE LANE" not in html.upper()
    audit_log = html.split('class="audit-log"', 1)[1].split("</ol>", 1)[0]
    assert re.search(r"<b>task\.[a-z_]+</b>", audit_log)
    assert '<p class="kpi-value kpi-value--text">abgeschlossen</p>' in html
    assert "private-overview-marker" not in html
    assert 'aria-current="step"' in html  # the focus task highlights its own step
    # Focus is the oldest waiting task, with the approval action.
    assert f'<a class="button quiet" href="/ui/tasks/{first.id}">Plan prüfen →</a>' in html
    assert 'href="/ui/audit.csv?limit=5"' in html
    # Exactly one filled primary action while approvals wait.
    assert '<a class="button" href="/ui/tasks#waiting-tasks">Freigaben öffnen</a>' in html
    assert '<a class="button quiet" href="/ui/tasks/new">＋ Neuer Auftrag</a>' in html
    head = db_session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if head:
        assert f'<p class="ov-stand">Schema {" + ".join(sorted(head))}</p>' in html
    act_on_task(db_session, first.id, "approve")
    db_session.commit()
    html = ui_client.get("/ui/overview").text
    assert "1 GO offen" in html and '<p class="kpi-value">1</p>' in html
    assert f'href="/ui/tasks/{first.id}">Plan prüfen' not in html


def test_overview_database_error_is_an_honest_gap(ui_client, monkeypatch):
    login(ui_client)

    def broken(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("synthetic outage"))

    monkeypatch.setattr("app.ui.routes.list_audit", broken)
    response = ui_client.get("/ui/overview")
    assert response.status_code == 503
    html = response.text
    assert "(Code: temporarily_unavailable)" in html
    assert "Auftragsdaten sind nicht verfügbar." in html
    assert "Audit-Daten sind nicht verfügbar." in html
    for claim in ("Kein Auftrag wartet auf dich.", "Noch keine Ereignisse erfasst.", "GO offen"):
        assert claim not in html
    assert "synthetic outage" not in html
    assert response.headers.get_list("content-security-policy") == [BOUNDARY_CSP]


@pytest.mark.parametrize("path", ["/ui/login", "/ui/overview", "/ui/assistant"])
def test_real_responses_carry_exactly_the_boundary_csp(ui_client, path):
    if path != "/ui/login":
        login(ui_client)
    response = ui_client.get(path)
    assert response.status_code == 200
    assert response.headers.get_list("content-security-policy") == [BOUNDARY_CSP]


def test_overview_states_assistant_facts_with_date_and_source(ui_client):
    login(ui_client)
    html = ui_client.get("/ui/overview").text
    assistant = html.split('id="assistent"', 1)[1].split("</details>", 1)[0]
    assert "Router-Golden-Set" in assistant and "Denkphase" in assistant
    assert "gemessen am 09.11.2026" in assistant and "docs/ASSISTENT.md" in assistant


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
    assert "<code>DECK_ROUTINE_WAKE</code>" in overview
    # Fable B8: duplicate DECK_* rows were removed, so three AN chips remain.
    assert overview.count('<span class="chip chip--on">AN</span>') >= 3
    assert "Routine-Wake: an" in pages["/ui/assistant"]
    monkeypatch.setenv("DECK_ROUTINE_WAKE_URL", "http://sentinel-plain.example.invalid/")
    overview = ui_client.get("/ui/overview").text
    assert '<span class="chip chip--warn">unvollständig</span>' in overview
    assert 'data-open-point="routine_wake"' in overview and "Sender unvollständig" in overview
    assert "sentinel" not in overview
