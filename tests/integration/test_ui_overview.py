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

ALEMBIC_HEAD = "20261110_0064"
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
    hints = html.split("<summary>Hinweise</summary>", 1)[1].split("</details>", 1)[0]
    assert 'data-open-point="lane_agy_pending"' in hints  # N1: a hint, not a warning
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
    # cq1: the shown revision is the script head; this PR adds no revision of its own.
    head = db_session.scalars(text("SELECT version_num FROM alembic_version")).all()
    assert head == [ALEMBIC_HEAD]
    assert f'<p class="ov-stand">Schema {ALEMBIC_HEAD}</p>' in html
    act_on_task(db_session, first.id, "approve")
    db_session.commit()
    html = ui_client.get("/ui/overview").text
    assert "1 GO offen" in html and '<p class="kpi-value">1</p>' in html
    assert f'href="/ui/tasks/{first.id}">Plan prüfen' not in html


def test_alembic_script_head_matches_the_documented_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == [ALEMBIC_HEAD]


@pytest.mark.parametrize(
    "target", ["list_audit", "list_owner_tasks", "read_lane_health", "read_settings"]
)
def test_overview_database_error_is_an_honest_gap(ui_client, monkeypatch, caplog, target):
    login(ui_client)

    def broken(*args, **kwargs):
        raise OperationalError("SELECT 'synthetic-secret'", {}, Exception("synthetic outage"))

    monkeypatch.setattr(f"app.ui.routes.{target}", broken)
    with caplog.at_level("WARNING", logger="app.ui.routes"):
        response = ui_client.get("/ui/overview")
    # Fable N10: exactly one log line, with the error class only and no values or SQL.
    lines = [r.getMessage() for r in caplog.records if r.name == "app.ui.routes"]
    assert lines == ["ui overview read failed: OperationalError"]
    assert response.status_code == 503
    html = response.text
    assert "(Code: temporarily_unavailable)" in html
    assert "Auftragsdaten sind nicht verfügbar." in html
    assert "Audit-Daten sind nicht verfügbar." in html
    for claim in ("Kein Auftrag wartet auf dich.", "Noch keine Ereignisse erfasst.", "GO offen"):
        assert claim not in html
    assert "synthetic outage" not in html and "synthetic-secret" not in html
    assert "Anbindung nicht verfügbar" in html and "lane-list" in html  # agy row only
    assert 'class="lane-row" data-lane="ollama"' not in html
    assert response.headers.get_list("content-security-policy") == [BOUNDARY_CSP]


def test_focus_is_only_the_oldest_waiting_task(ui_client, db_session):
    # cq1: a newer active task does not take the focus; docs/UI.md names the waiting task.
    login(ui_client)
    running = _insert_task(db_session, approved=True)
    db_session.commit()
    assert running.status == TaskState.QUEUED  # active, but nothing waits on the owner
    html = ui_client.get("/ui/overview").text
    assert "Kein Auftrag wartet auf dich." in html
    assert f'href="/ui/tasks/{running.id}"' not in html
    assert 'aria-current="step"' not in html


def test_messages_tab_opens_the_section_without_javascript(ui_client):
    login(ui_client)
    pattern = '<details class="panel ov-card ov-fold" id="meldungen" open>'
    opened = ui_client.get("/ui/overview?open=meldungen")
    assert opened.status_code == 200 and pattern in opened.text
    assert 'href="/ui/overview?open=meldungen#meldungen"' in opened.text
    for value in ("", "audit", "meldungen<script>", "MELDUNGEN"):
        html = ui_client.get("/ui/overview", params={"open": value}).text
        assert pattern not in html, value
        assert "<script>" not in html.split("<main", 1)[1]


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
    # Fable B8: duplicate DECK_* rows were removed, so exactly three AN chips remain:
    # the loopback chip and the web switch in "Assistent", the wake chip in "Meldungen".
    on = '<span class="chip chip--on">AN</span>'
    assert overview.count(on) == 3
    assistant = overview.split('id="assistent"', 1)[1].split("</details>", 1)[0]
    messages = overview.split('id="meldungen"', 1)[1].split("</details>", 1)[0]
    assert assistant.split("</summary>", 1)[0].count(on) == 1
    assert "<code>DECK_ASSISTANT_WEB</code></dt><dd>" + on in assistant
    assert messages.split("</summary>", 1)[0].count(on) == 1
    assert "Routine-Wake: an" in pages["/ui/assistant"]
    monkeypatch.setenv("DECK_ROUTINE_WAKE_URL", "http://sentinel-plain.example.invalid/")
    overview = ui_client.get("/ui/overview").text
    assert '<span class="chip chip--warn">unvollständig</span>' in overview
    assert 'data-open-point="routine_wake"' in overview and "Sender unvollständig" in overview
    assert "sentinel" not in overview
