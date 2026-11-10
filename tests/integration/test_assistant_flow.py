"""Assistant UI uses the existing gate and does not grant the mock any rights."""

from __future__ import annotations

import json
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.assistant.cards import PRESET_IDS, PRESETS, versions_for
from app.assistant.dialogue import write_profile
from app.assistant.errors import AssistantError
from app.assistant.records import AssistantProfileVersion, AssistantTurn
from app.db.models import Task
from app.db.state_machine import TaskState
from app.ui.chat import SimulatedChat

PASSWORD = "synthetic-ui-owner-password"


class Inputs(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.by_id: dict[str, str] = {}
        self.csrf = ""
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        data = {key: value or "" for key, value in attrs}
        if data.get("id"):
            self.by_id[data["id"]] = data.get("value", "")
        if data.get("name") == "csrf" and not self.csrf:
            self.csrf = data.get("value", "")


@pytest.fixture
def ui_client(db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", PasswordHasher().hash(PASSWORD))
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    monkeypatch.setenv("DECK_INBOX_DIR", str(tmp_path / "inbox"))
    app = create_app()
    app.state.local_chat = SimulatedChat()

    def database():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client


def login(client: TestClient):
    page = client.get("/ui/login")
    token = Inputs(page.text).csrf
    response = client.post(
        "/ui/login",
        data={"csrf": token, "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    home = client.get("/ui/assistant")
    assert home.status_code == 200
    return home


def test_assistant_page_requires_login(ui_client: TestClient) -> None:
    response = ui_client.get("/ui/assistant", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/ui/login")


def test_dialogue_does_not_create_or_approve_a_task(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    assert 'name="confirmation_role"' not in home.text
    assert "nicht gebaut" in home.text
    assert "kein Zeitlimit-Resultat" in home.text
    shown = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": parsed.csrf,
            "message": "Wie geht es dir heute Morgen",
            "data_class": "local_only",
        },
        follow_redirects=True,
    )
    assert "Nicht gespeichert" in shown.text
    assert "Aufgenommen" in shown.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    assert db_session.scalar(select(func.count()).select_from(AssistantTurn)) == 0
    missing = ui_client.post(
        "/ui/assistant/talk",
        data={"csrf": parsed.csrf, "message": "Hallo"},
        follow_redirects=False,
    )
    assert missing.status_code == 422
    forged = ui_client.post(
        "/ui/assistant/talk",
        data={"message": "Hallo", "data_class": "local_only"},
        follow_redirects=False,
    )
    assert forged.status_code == 403
    assert ui_client.get("/ui/assistant/approve").status_code == 404


def test_streaming_talk_works_with_test_database_only(
    ui_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Background turns must not require DECK_DATABASE_URL (CI sets only the test URL)."""
    monkeypatch.delenv("DECK_DATABASE_URL", raising=False)
    home = login(ui_client)
    parsed = Inputs(home.text)
    response = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": parsed.csrf,
            "message": "Wie geht es dir heute Morgen",
            "data_class": "local_only",
            "stream": "yes",
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 202
    turn_id = response.json()["turn_id"]
    deadline = time.monotonic() + 30.0
    snapshot: dict[str, object] = {}
    while time.monotonic() < deadline:
        snapshot = ui_client.get(f"/ui/assistant/talk/{turn_id}").json()
        if snapshot["state"] == "done":
            break
        if snapshot["state"] == "error":
            pytest.fail(str(snapshot.get("error")))
        time.sleep(0.05)
    else:
        pytest.fail("streaming dialogue turn did not finish")
    assert snapshot.get("persisted") is False
    assert snapshot.get("answer")


def test_persistence_and_profile_are_owner_controlled(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    saved = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": parsed.csrf,
            "message": "Wie geht es dir heute Morgen",
            "data_class": "local_only",
            "persist": "yes",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AssistantTurn)) == 1
    assert "Gespeichert, vom Owner gewählt" in saved.text
    profile = ui_client.post(
        "/ui/assistant/profile",
        data={"csrf": parsed.csrf, "notes": "Nur der Owner"},
        follow_redirects=True,
    )
    assert profile.status_code == 200
    assert "Profil gespeichert" in profile.text
    with pytest.raises(AssistantError) as rejected:
        write_profile(db_session, "vom modell", actor="model")
    assert rejected.value.code == "profile_owner_only"
    versions = db_session.scalars(
        select(AssistantProfileVersion).order_by(AssistantProfileVersion.version)
    ).all()
    assert [row.notes for row in versions] == ["Nur der Owner"]


def test_card_run_stays_behind_the_existing_gate(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    card_id = PRESET_IDS["coding"]
    key = parsed.by_id[f"card-key-{card_id}"]
    first = ui_client.post(
        f"/ui/assistant/cards/{card_id}/run",
        data={
            "csrf": parsed.csrf,
            "idempotency_key": key,
            "ph_thema": "Button",
            "data_class": "cloud_ok",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = ui_client.post(
        f"/ui/assistant/cards/{card_id}/run",
        data={
            "csrf": parsed.csrf,
            "idempotency_key": key,
            "ph_thema": "Anders",
            "data_class": "cloud_ok",
        },
        follow_redirects=False,
    )
    assert second.status_code == 303 and second.headers["location"] == first.headers["location"]
    task = db_session.scalar(select(Task))
    assert task is not None
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1
    assert task.status == TaskState.AWAITING_APPROVAL and task.approved_at is None
    assert task.lease is None
    assert task.payload["data_class"] == "local_only"
    assert "Button" in task.payload["instructions"]
    assert "Anders" not in task.payload["instructions"]
    assert task.payload["fallback_chain"] == ["ollama"]
    assert task.payload["labels"]["confirmation_role"] == "Bote ohne eigene Rechte"
    assert "kein Live-Modell" in task.payload["labels"]["confirmation_path"]
    order = ui_client.get(first.headers["location"])
    assert "Freigabe offen" in order.text
    assert "Start freigeben" not in order.text
    assert "Zur bestehenden Auftragsseite" in order.text


def test_owner_edit_keeps_history_and_presets_are_not_reset(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    card_id = PRESET_IDS["coding"]
    original = next(item.template for item in PRESETS if item.id == card_id)
    updated = ui_client.post(
        f"/ui/assistant/cards/{card_id}",
        data={
            "csrf": parsed.csrf,
            "name": "Coding-Hilfe",
            "category": "code",
            "data_class": "local_only",
            "placeholders": "thema",
            "template": "Erklaere {{thema}} neu.",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    db_session.expire_all()
    rows = versions_for(db_session, card_id)
    assert [row.template for row in rows] == [original, "Erklaere {{thema}} neu."]
    again = ui_client.get("/ui/assistant")
    assert "Erklaere {{thema}} neu." in again.text


def test_shell_text_is_stored_as_an_order_not_run(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    created = ui_client.post(
        "/ui/assistant/cards",
        data={
            "csrf": parsed.csrf,
            "name": "Shelltext",
            "category": "frage",
            "data_class": "local_only",
            "placeholders": "thema",
            "template": "Zeige {{thema}} und $(echo hi)",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    page = Inputs(ui_client.get("/ui/assistant").text)
    card_ids = [
        item
        for item in page.by_id
        if item.startswith("card-key-") and PRESET_IDS["coding"].hex not in item
    ]
    assert card_ids
    key_id = card_ids[-1]
    card_uuid = key_id.removeprefix("card-key-")
    run = ui_client.post(
        f"/ui/assistant/cards/{card_uuid}/run",
        data={"csrf": page.csrf, "idempotency_key": page.by_id[key_id], "ph_thema": "zeigen"},
        follow_redirects=True,
    )
    assert run.status_code == 200
    task = db_session.scalars(select(Task)).all()[-1]
    assert "$(echo hi)" in task.payload["instructions"]
    assert task.status == TaskState.AWAITING_APPROVAL


def test_import_rejects_a_secret_and_search_can_be_saved(
    ui_client: TestClient, db_session: Session
) -> None:
    home = login(ui_client)
    parsed = Inputs(home.text)
    secret = {
        "schema_version": "assistant_card.v1",
        "name": "Geheim",
        "category": "frage",
        "template": "Nutze sk-benchmarktrap1 fuer {{thema}}.",
        "placeholders": ["thema"],
        "data_class": "local_only",
    }
    rejected = ui_client.post(
        "/ui/assistant/cards/import",
        data={"csrf": parsed.csrf, "raw": json.dumps(secret)},
        follow_redirects=False,
    )
    assert rejected.status_code == 422
    corpus = json.dumps(
        [{"title": "Notiz Kiefer", "published_on": "2026-01-02", "text": "Wort kiefer steht hier."}]
    )
    blocked = ui_client.post(
        "/ui/assistant/search",
        data={
            "csrf": parsed.csrf,
            "idempotency_key": parsed.by_id["search-key"],
            "question": "aktuelle Nachrichten",
            "data_class": "local_only",
            "corpus": corpus,
        },
        follow_redirects=True,
    )
    assert "keinen Netzabruf" in blocked.text
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    fresh = Inputs(ui_client.get("/ui/assistant").text)
    saved = ui_client.post(
        "/ui/assistant/search",
        data={
            "csrf": fresh.csrf,
            "idempotency_key": fresh.by_id["search-key"],
            "question": "Finde kiefer",
            "data_class": "local_only",
            "corpus": corpus,
            "save_order": "yes",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200 and "Freigabe offen" in saved.text
    again = ui_client.post(
        "/ui/assistant/search",
        data={
            "csrf": fresh.csrf,
            "idempotency_key": fresh.by_id["search-key"],
            "question": "Finde kiefer",
            "data_class": "local_only",
            "corpus": corpus,
            "save_order": "yes",
        },
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1


def test_opt_in_loopback_stores_dialogue_without_creating_a_task(
    ui_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            captured.append(raw)
            body = json.loads(raw)
            line = (
                json.dumps(
                    {
                        "model": body["model"],
                        "message": {"role": "assistant", "content": "FIXTURE_REPLY_7"},
                        "done": True,
                        "done_reason": "stop",
                    }
                ).encode()
                + b"\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(line)))
            self.end_headers()
            self.wfile.write(line)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = login(ui_client)
        parsed = Inputs(home.text)
        profile = ui_client.post(
            "/ui/assistant/profile",
            data={"csrf": parsed.csrf, "notes": "Notiz PROFILTOKEN"},
            follow_redirects=True,
        )
        assert profile.status_code == 200
        db_session.add(
            AssistantTurn(
                data_class="cloud_ok",
                owner_text="FREMDKLASSE",
                reply_text="fremd",
                reply_origin="mock",
            )
        )
        db_session.commit()
        port = server.server_address[1]
        monkeypatch.setenv("DECK_ASSISTANT_OLLAMA", "1")
        monkeypatch.setenv("DECK_OLLAMA_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("DECK_ASSISTANT_OLLAMA_MODEL", "fixture:localtest")
        page = Inputs(ui_client.get("/ui/assistant").text)
        saved = ui_client.post(
            "/ui/assistant/talk",
            data={
                "csrf": page.csrf,
                "message": "Wie geht es dir heute Morgen",
                "data_class": "local_only",
                "include_profile": "yes",
                "include_history": "yes",
                "persist": "yes",
            },
            follow_redirects=True,
        )
    finally:
        server.shutdown()
    assert saved.status_code == 200
    assert "FIXTURE_REPLY_7" in saved.text
    assert "ollama_loopback" in saved.text
    assert "Nicht gespeichert" not in saved.text
    body = captured[0].decode()
    assert "PROFILTOKEN" in body and "FREMDKLASSE" not in body
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    origin = db_session.scalar(
        select(AssistantTurn.reply_origin).order_by(AssistantTurn.created_at.desc())
    )
    assert origin == "ollama_loopback"
