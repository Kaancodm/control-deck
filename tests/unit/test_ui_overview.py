"""Overview projections: fixed steps, honest lane labels, derived open points, flag states."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.audit.health import LaneHealthView
from app.audit.presentation import AuditItem
from app.core.schemas import Worker
from app.db.settings import RunnerSettings
from app.db.state_machine import TaskState
from app.ui import overview, views
from app.ui.overview import FeatureFlags, Outcome

NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
FLAG_VARIABLES = (
    "DECK_ASSISTANT_OLLAMA",
    "DECK_OLLAMA_URL",
    "DECK_ASSISTANT_OLLAMA_MODEL",
    "DECK_ASSISTANT_WEB",
    "DECK_ASSISTANT_WEB_ALLOWLIST",
    "DECK_ASSISTANT_SEARCH_PROVIDER",
    "DECK_ASSISTANT_MEDIAWIKI_API",
    "DECK_ASSISTANT_SEARXNG_URL",
    "DECK_ROUTINE_WAKE",
    "DECK_ROUTINE_WAKE_URL",
    "DECK_ROUTINE_WAKE_AUTHORIZATION",
)


def lane(worker: Worker = Worker.OLLAMA, **values: object) -> LaneHealthView:
    base: dict[str, object] = {
        "lane": worker,
        "enabled": True,
        "paused": False,
        "status": "ok",
        "reason": "simulation_ready",
        "execution_mode": "simulation",
        "live_available": False,
        "daily_attempt_limit": 20,
        "claims_today": 3,
        "configured_attempts_remaining": 17,
        "observed_at": NOW,
        "last_observation_success_at": NOW,
        "simulated_attempts": 0,
        "remote_attempts": 0,
        "unknown_attempts": 0,
        "completed_attempts": 0,
        "consecutive_failures": 0,
        "last_error_code": None,
        "last_error_at": None,
        "last_attempt_at": None,
        "last_attempt_success_at": None,
        "warnings": [],
    }
    return LaneHealthView.model_validate(base | values)


def flags(**values: object) -> FeatureFlags:
    base: dict[str, object] = {
        "assistant_loopback": "on",
        "model_named": True,
        "assistant_web": "on",
        "encyclopedia": True,
        "web_search": False,
        "routine_wake": "on",
    }
    return FeatureFlags(**(base | values))  # type: ignore[arg-type]


@pytest.fixture
def clean_env(monkeypatch):
    for name in FLAG_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("state", list(TaskState))
def test_workflow_steps_cover_every_task_state(state: TaskState) -> None:
    steps = overview.workflow_steps(state.value)
    assert [step.number for step in steps] == ["01", "02", "03", "04", "05", "06"]
    assert [step.label for step in steps] == [
        "Geplant",
        "Start-Freigabe",
        "Wartend",
        "Zugewiesen",
        "Läuft",
        "Review",
    ]
    states = [step.state for step in steps]
    order = ["awaiting_approval", "queued", "leased", "running", "review"]
    if state.value in order:
        current = order.index(state.value) + 1
        assert states == ["done"] * current + ["current"] + ["todo"] * (5 - current)
    elif state == TaskState.DONE:
        assert states == ["done"] * 6
    else:
        # Terminal failures and historical rows highlight nothing.
        assert states == ["todo"] * 6


def test_workflow_steps_without_focus_are_a_legend() -> None:
    assert {step.state for step in overview.workflow_steps(None)} == {"todo"}


def test_claims_summary_with_and_without_limits() -> None:
    assert overview.claims_summary([]) is None
    full = overview.claims_summary(
        [lane(claims_today=2, daily_attempt_limit=10), lane(Worker.CLAUDE, claims_today=5)]
    )
    assert full == overview.Claims(total=7, limit=30)
    partial = overview.claims_summary(
        [lane(claims_today=2), lane(Worker.CODEX, claims_today=1, daily_attempt_limit=None)]
    )
    assert partial == overview.Claims(total=3, limit=None)


@pytest.mark.parametrize(
    "values,label,tone",
    [
        ({"enabled": False, "status": "auth_required"}, "Deaktiviert", "off"),
        ({"status": "auth_required", "paused": True}, "Anmeldung nötig", "warn"),
        ({"paused": True, "status": "unavailable"}, "Pausiert", "off"),
        ({"status": "paused"}, "Pausiert", "off"),
        ({"status": "unavailable", "configured_attempts_remaining": 0}, "Nicht verfügbar", "warn"),
        (
            {"configured_attempts_remaining": 0, "live_available": True},
            "Lokales Tageslimit erreicht",
            "warn",
        ),
        ({"live_available": True, "execution_mode": "live"}, "Bereit", "on"),
        ({"execution_mode": "simulation"}, "Simulation", "off"),
        ({"execution_mode": "live", "live_available": False}, "Vorprüfung ok", "on"),
        ({"status": "unknown", "execution_mode": "unknown"}, "Unbekannt", "off"),
    ],
)
def test_lane_rows_rank_labels(values: dict[str, object], label: str, tone: str) -> None:
    (row,) = overview.lane_rows([lane(**values)])
    assert (row.label, row.tone) == (label, tone)
    assert row.lane == "ollama" and row.kind == "lokal"


def test_lane_rows_keep_cli_kind_and_counters() -> None:
    (row,) = overview.lane_rows([lane(Worker.CODEX, claims_today=4, daily_attempt_limit=None)])
    assert (row.kind, row.claims, row.limit) == ("CLI", 4, None)


def audit_item(phase: str | None, minutes: int) -> AuditItem:
    return AuditItem(
        source="operation",
        id=minutes,
        created_at=NOW - timedelta(minutes=minutes),
        event_type="operation.backup",
        phase=phase,
    )


def test_latest_outcome_skips_started_and_takes_newest() -> None:
    assert overview.latest_outcome([]) is None
    assert overview.latest_outcome([audit_item("started", 1)]) is None
    outcome = overview.latest_outcome(
        [audit_item("started", 1), audit_item("failed", 2), audit_item("completed", 3)]
    )
    assert outcome == Outcome("failed", NOW - timedelta(minutes=2))


def test_open_points_follow_fixed_order_and_codes() -> None:
    points = overview.open_points(
        controls=RunnerSettings(paused=True, stop_requested=True),
        waiting_total=2,
        blocked_total=1,
        lanes=[lane(Worker.CLAUDE, warnings=["auth_required", "no_recent_success"])],
        health_unavailable=True,
        flags=flags(assistant_loopback="incomplete", assistant_web="off", routine_wake="off"),
        last_backup=None,
        last_restore=None,
        pending_lanes=(),
    )
    assert [point.code for point in points] == [
        "stop",
        "paused",
        "approvals",
        "blocked",
        "lane_auth_required",
        "lane_no_recent_success",
        "health_unavailable",
        "assistant_loopback",
        "assistant_web",
        "routine_wake",
        "backup_missing",
        "restore_missing",
    ]
    assert points[2].title == "2 Freigaben offen"
    assert points[2].href == "/ui/tasks#waiting-tasks"
    assert points[3].href == "/ui/tasks?state=blocked"
    assert points[7].chip == "unvollständig" and points[8].chip == "aus"
    assert points[9].href == "/ui/overview#meldungen"
    assert all(point.href is None or point.href.startswith("/ui/") for point in points)


def test_open_points_are_empty_when_everything_is_recorded() -> None:
    done = Outcome("completed", NOW)
    assert (
        overview.open_points(
            controls=RunnerSettings(),
            waiting_total=0,
            blocked_total=0,
            lanes=[lane()],
            health_unavailable=False,
            flags=flags(),
            last_backup=done,
            last_restore=done,
            pending_lanes=(),
        )
        == ()
    )
    failed = Outcome("failed", NOW)
    codes = [
        point.code
        for point in overview.open_points(
            controls=None,
            waiting_total=0,
            blocked_total=0,
            lanes=[],
            health_unavailable=False,
            flags=flags(),
            last_backup=failed,
            last_restore=failed,
        )
    ]
    assert codes == ["lane_agy_pending", "backup_failed", "restore_failed"]


def test_agy_is_a_pending_lane_without_data() -> None:
    # Owner decision of 07.11.2026: agy is the fourth lane; the deck does not connect it yet.
    assert [lane.key for lane in overview.PENDING_LANES] == ["agy"]
    assert "agy" not in {worker.value for worker in Worker}  # enum stays unchanged
    (pending,) = overview.PENDING_LANES
    assert pending.label == "eigene Lane · im Deck noch nicht angebunden"
    assert "KEINE LANE" not in pending.label.upper()
    summary = overview.lane_summary([lane(), lane(Worker.CLAUDE)], health_unavailable=False)
    assert summary == overview.LaneSummary(total=4, connected=2)
    assert overview.lane_summary([], health_unavailable=True).connected is None
    point = next(
        p
        for p in overview.open_points(
            controls=None,
            waiting_total=0,
            blocked_total=0,
            lanes=[],
            health_unavailable=False,
            flags=flags(),
            last_backup=None,
            last_restore=None,
        )
        if p.code == "lane_agy_pending"
    )
    assert point.title == "agy-Lane: ADR und Anbindung fehlen" and point.tone == "warn"


def test_only_warnings_count_as_open_points_on_an_empty_deck() -> None:
    points = overview.open_points(
        controls=RunnerSettings(),
        waiting_total=0,
        blocked_total=0,
        lanes=[lane()],
        health_unavailable=False,
        flags=flags(assistant_loopback="off", assistant_web="off", routine_wake="off"),
        last_backup=None,
        last_restore=None,
        pending_lanes=(),
    )
    assert [p.code for p in points if p.tone == "warn"] == ["backup_missing"]
    assert [p.code for p in points if p.tone != "warn"] == [
        "assistant_loopback",
        "assistant_web",
        "routine_wake",
        "restore_missing",
    ]


def test_wake_facts_match_the_outbox() -> None:
    from app.transport import outbox

    assert overview.WAKE_MAX_POSTS == outbox._MAX_POSTS
    texts = " ".join(f"{fact.label} {fact.value}" for fact in overview.WAKE_FACTS)
    assert "ausser HTTP 200" in texts and "nur cloud_ok" in texts
    assert "kein automatischer Neuversand" in texts


def test_assistant_facts_come_from_code_constants(clean_env) -> None:
    from app.assistant.ollama_chat import DIALOGUE_NUM_CTX_DEFAULT, DIALOGUE_NUM_PREDICT_DEFAULT
    from app.workers.lanes.ollama_http import BUSY_ATTEMPTS

    for name in (
        "DECK_ASSISTANT_OLLAMA_NUM_CTX",
        "DECK_ASSISTANT_OLLAMA_NUM_PREDICT",
        "DECK_ASSISTANT_OLLAMA_KEEP_ALIVE",
    ):
        clean_env.delenv(name, raising=False)
    facts = {fact.label: fact.value for fact in overview.assistant_facts()}
    assert facts["Kontextbudget"] == (
        f"{DIALOGUE_NUM_CTX_DEFAULT} Token Kontext, "
        f"Antwort höchstens {DIALOGUE_NUM_PREDICT_DEFAULT} Token"
    )
    assert facts["Denkphase"] == "aus"
    assert f"bis zu {BUSY_ATTEMPTS} Versuche" in facts["Bei Auslastung"]
    assert "Ollama ausgelastet." in facts["Bei Auslastung"]
    assert facts["Router-Golden-Set"] == "nur offline"
    # A measurement is shown only with its date and documented source, never as a promise.
    assert "gemessen am 09.11.2026" in facts["Antwortzeit"]
    assert "docs/ASSISTENT.md" in facts["Antwortzeit"] and "keine Zusage" in facts["Antwortzeit"]
    docs = (Path(views.__file__).parents[2] / "docs" / "ASSISTENT.md").read_text(encoding="utf-8")
    assert "Gemessen am 2026-11-09 auf dem Server" in docs
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_NUM_CTX", "8192")
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_KEEP_ALIVE", "sentinel-keep-7411")
    facts = {fact.label: fact.value for fact in overview.assistant_facts()}
    assert facts["Kontextbudget"].startswith("8192 Token")
    assert "sentinel" not in repr(facts) and "7411" not in repr(facts)
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_NUM_CTX", "99999")
    facts = {fact.label: fact.value for fact in overview.assistant_facts()}
    assert facts["Kontextbudget"] == "Konfiguration ungültig"


def test_feature_flags_default_off(clean_env) -> None:
    assert overview.feature_flags() == FeatureFlags("off", False, "off", False, False, "off")


def test_feature_flags_env_matrix(clean_env) -> None:
    clean_env.setenv("DECK_ASSISTANT_OLLAMA", "1")
    clean_env.setenv("DECK_ASSISTANT_WEB", "1")
    clean_env.setenv("DECK_ROUTINE_WAKE", "1")
    state = overview.feature_flags()
    assert (state.assistant_loopback, state.assistant_web, state.routine_wake) == (
        "incomplete",
        "incomplete",
        "incomplete",
    )
    assert not state.model_named and not state.encyclopedia and not state.web_search
    clean_env.setenv("DECK_OLLAMA_URL", "http://127.0.0.1:1")
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_MODEL", "synthetic-model:tag")
    clean_env.setenv("DECK_ASSISTANT_WEB_ALLOWLIST", "example.invalid")
    clean_env.setenv("DECK_ASSISTANT_SEARCH_PROVIDER", "mediawiki")
    clean_env.setenv("DECK_ASSISTANT_MEDIAWIKI_API", "https://example.invalid/w/api.php")
    clean_env.setenv("DECK_ROUTINE_WAKE_URL", "https://example.invalid/wake")
    clean_env.setenv("DECK_ROUTINE_WAKE_AUTHORIZATION", "Bearer synthetic-wake-key")
    state = overview.feature_flags()
    assert state == FeatureFlags("on", True, "on", True, False, "on")
    clean_env.setenv("DECK_ASSISTANT_SEARCH_PROVIDER", "searxng")
    assert overview.feature_flags().assistant_web == "incomplete"
    clean_env.setenv("DECK_ASSISTANT_SEARXNG_URL", "https://example.invalid/search")
    state = overview.feature_flags()
    assert (state.assistant_web, state.encyclopedia, state.web_search) == ("on", False, True)
    clean_env.setenv("DECK_ROUTINE_WAKE_URL", "http://example.invalid/wake")
    assert overview.feature_flags().routine_wake == "incomplete"
    clean_env.setenv("DECK_ROUTINE_WAKE", "true")
    assert overview.feature_flags().routine_wake == "off"


def test_feature_flags_never_carry_values(clean_env) -> None:
    clean_env.setenv("DECK_ASSISTANT_OLLAMA_MODEL", "sentinel-model-7411")
    state = overview.feature_flags()
    assert "sentinel" not in repr(state)
    assert all(isinstance(value, (bool, str)) for value in vars(state).values())
    assert set(vars(state).values()) <= {True, False, "on", "off", "incomplete"}


def test_network_nodes_reflect_real_states() -> None:
    nodes = overview.network_nodes(
        lanes=[lane(), lane(Worker.CLAUDE, paused=True)],
        flags=flags(assistant_loopback="off"),
        last_backup=None,
    )
    by_key = {node.key: node for node in nodes}
    assert list(by_key) == [
        "owner",
        "ollama",
        "claude",
        "codex",
        "agy",
        "assistant",
        "database",
        "messages",
        "backup",
    ]
    assert by_key["ollama"].active and by_key["ollama"].label == "ollama"
    assert by_key["ollama"].state == "Simulation"
    assert not by_key["claude"].active and by_key["claude"].state == "Pausiert"
    # codex has no health row: unknown, never "aus" without a source.
    assert not by_key["codex"].active and by_key["codex"].state == "nicht verfügbar"
    assert not by_key["agy"].active and by_key["agy"].state == "nicht angebunden"
    assert not by_key["assistant"].active and by_key["assistant"].state == "aus"
    assert by_key["messages"].active and by_key["messages"].state == "an"
    assert not by_key["backup"].active and by_key["backup"].state == "nicht erfasst"
    assert all(0 <= node.x <= 400 and 0 <= node.y <= 250 for node in nodes)
    assert all(0 <= node.label_x <= 400 and 0 <= node.label_y <= 250 for node in nodes)


def test_network_lanes_without_health_data_are_not_available_not_off() -> None:
    nodes = overview.network_nodes(
        lanes=[], flags=flags(), last_backup=None, health_unavailable=True
    )
    states = {node.key: node.state for node in nodes if node.kind == "lane"}
    assert states == {
        "ollama": "nicht verfügbar",
        "claude": "nicht verfügbar",
        "codex": "nicht verfügbar",
        "agy": "nicht angebunden",
    }
    loaded = overview.network_nodes(
        lanes=[lane(enabled=False)], flags=flags(), last_backup=None, health_unavailable=True
    )
    assert {n.key: n.state for n in loaded}["ollama"] == "nicht verfügbar"
    html = render_overview(
        net=nodes, flags=flags(), lane_rows=(), pending_lanes=overview.PENDING_LANES
    )
    assert "· aus" not in html and "KEINE LANE" not in html.upper()
    assert 'data-node-state="codex"' in html


def render_overview(**context: object) -> str:
    base = {"owner": True, "csrf": "synthetic-csrf", "controls": None, "lane_health": []}
    return views.TEMPLATES.env.get_template("overview.html").render({**base, **context})


def visible_text(html: str) -> str:
    body = html.split("</head>", 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_overview_without_data_shows_no_invented_numbers() -> None:
    html = render_overview()
    text = visible_text(html)
    digits = re.findall(r"[\w-]*\d[\w-]*", text)
    assert set(digits) <= {"CD-01", "01", "02", "03", "04", "05", "06"}, digits
    assert "noch keines erfasst" in text and "Restore-Probe: keine erfasst" in text
    assert text.count("nicht verfügbar") >= 3
    assert "Kein Auftrag wartet auf dich." in text
    assert "Keine offenen Punkte aus den Deck-Daten." in text
    assert "GO offen" not in text


def test_overview_shows_sentinel_numbers_exactly_where_they_belong() -> None:
    html = render_overview(
        waiting_total=7411,
        claims=overview.Claims(total=6203, limit=None),
        lane_rows=(),
        open_points=(),
        flags=flags(),
        activation=(),
        audit=[],
        steps=overview.workflow_steps(None),
        nav_counts={},
    )
    text = visible_text(html)
    assert text.count("7411") == 2  # top bar badge and the approvals tile
    assert "7411 GO offen" in text
    assert text.count("6203") == 1
    digits = set(re.findall(r"[\w-]*\d[\w-]*", text)) - {"7411", "6203", "CD-01"}
    assert digits == {"01", "02", "03", "04", "05", "06"}


def test_overview_keeps_locator_contracts() -> None:
    html = render_overview(flags=flags(), activation=("Synthetische Zeile.",))
    main = html.split("<main", 1)[1].split("</main>", 1)[0]
    names = [
        re.sub(r"<[^>]+>|\s+", " ", link).strip()
        for link in re.findall(r"<a\b[^>]*>(.*?)</a>", main, re.S)
    ]
    assert not {"Audit", "Lanes", "Wissen"} & set(names)  # one visible link per target (K5)
    assert names.count("＋ Neuer Auftrag") == 1  # K2
    assert "data-lane-warning" not in main  # K9
    assert main.count("<h1") == 1
    for anchor in ("flow", "lanes", "assistent", "meldungen", "offen", "audit"):
        assert f'id="{anchor}"' in main


def _point(code: str, tone: str) -> overview.OpenPoint:
    return overview.OpenPoint(code, f"Titel {code}", "Detail", "chip", tone, None)  # type: ignore[arg-type]


def test_overview_order_follows_the_phone_reading_order() -> None:
    html = render_overview(flags=flags(), lanes_summary=overview.LaneSummary(4, 3))
    main = html.split("<main", 1)[1]
    order = [
        main.index(marker)
        for marker in (
            "<h1",
            'class="panel hero"',
            'class="kpis"',
            'id="offen"',
            'id="flow"',
            'id="lanes"',
            'id="assistent"',
            'id="meldungen"',
            'id="audit"',
        )
    ]
    assert order == sorted(order)
    for anchor in ("assistent", "meldungen", "audit"):
        assert re.search(rf'<details class="panel ov-card ov-fold" id="{anchor}"[ >]', main)


def test_exactly_one_filled_primary_action() -> None:
    def filled(html: str) -> list[str]:
        head = html.split('class="ov-head-actions"', 1)[1].split("</div>", 1)[0]
        return re.findall(r'<a class="button" href="([^"]+)"', head)

    assert filled(render_overview(waiting_total=0)) == ["/ui/tasks/new"]
    busy = render_overview(waiting_total=2)
    assert filled(busy) == ["/ui/tasks#waiting-tasks"]
    assert '<a class="button quiet" href="/ui/tasks/new">＋ Neuer Auftrag</a>' in busy
    assert "Freigaben öffnen ↗" not in busy and "Lanes ansehen" not in busy  # no hero buttons


def test_open_point_counter_counts_warnings_and_folds_hints() -> None:
    points = tuple(_point(f"w{index}", "warn") for index in range(5)) + (_point("hint", "off"),)
    html = render_overview(open_points=points)
    section = html.split('id="offen"', 1)[1].split("</section>", 1)[0]
    assert '<span class="chip chip--warn">5 offen</span>' in section
    visible, rest = section.split("<details", 1)
    assert visible.count("data-open-point=") == 3
    assert "2 weitere" in rest and 'data-open-point="w4"' in rest
    hints = section.split("Hinweise zur Konfiguration", 1)[1]
    assert 'data-open-point="hint"' in hints
    quiet = render_overview(open_points=(_point("hint", "off"),))
    assert "Keine offenen Punkte aus den Deck-Daten." in quiet and " offen</span>" not in quiet


def test_project_status_is_folded_dated_and_marked_static() -> None:
    html = render_overview(
        project_status=overview.PROJECT_STATUS,
        project_status_date=overview.PROJECT_STATUS_DATE,
    )
    block = html.split('data-static="project-status"', 1)[1].split("</details>", 1)[0]
    assert "Projekt-Stand (statisch, Stand 10.11.2026)" in block
    assert "keine Live-Daten" in block
    for marker in ("iPhone", "agy-Lane", "#63", "#59", "#54 bis #57"):
        assert marker in block


def test_unavailable_data_is_not_shown_as_empty() -> None:
    html = render_overview(data_unavailable=True, error="temporarily_unavailable")
    text = visible_text(html)
    assert "Kein Auftrag wartet auf dich." not in text
    assert "Noch keine Ereignisse erfasst." not in text
    assert "noch keines erfasst" not in text
    assert "Keine offenen Punkte" not in text
    assert "Auftragsdaten sind nicht verfügbar." in text
    assert "vorübergehend nicht verfügbar" in text


def test_flag_states_render_as_states_only() -> None:
    html = render_overview(flags=flags(assistant_loopback="incomplete", routine_wake="off"))
    assert "UNVOLLSTÄNDIG" in html and "AUS" in html
    assert "<code>DECK_ROUTINE_WAKE</code>" in html


@pytest.mark.parametrize("name", ["overview.html", "base.html", "login.html"])
def test_shell_sources_carry_no_mockup_sample_data(name: str) -> None:
    source = (Path(views.__file__).with_name("templates") / name).read_text(encoding="utf-8")
    for marker in (
        "Beispiel",
        "qwen",
        "abliterated",
        "Ubuntu",
        "5433",
        "PR64",
        "PR65",
        "agy",
        "Gemini",
        "04:00",
        "Tailnet",
    ):
        assert marker not in source, (name, marker)
