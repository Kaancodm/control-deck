"""Deck memory inbox: approval, a separate clear, and the conversation audit reader."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.assistant.memory_dialogue import approve_displayed_reply, conduct_dialogue
from app.assistant.records import AssistantTurn
from app.memory.approval import (
    correct_fact,
    correct_message,
    decide_fact,
    decide_message,
    fact_shown_bytes,
    message_shown_bytes,
    propose_fact,
    shown_sha256,
)
from app.memory.ingest import IngestOutcome, ingest_turn, record_deck_reply
from app.memory.models import AssistantMessage, MemoryApproval, MemoryFact
from app.memory.policy import ConversationError
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE

PASSWORD = "synthetic-ui-owner-password"
SENTENCE = "Synthetic quarantine sentence about the blue kettle."


class Inputs(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.inputs: dict[str, str] = {}
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("name"):
            self.inputs[str(values["name"])] = values.get("value") or ""


class Forms(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.forms: list[dict[str, object]] = []
        self._form: dict[str, object] | None = None
        self._button: list[str] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._form = {"action": values.get("action") or "", "inputs": {}, "button": ""}
        elif self._form is not None and tag == "input" and values.get("name"):
            inputs = self._form["inputs"]
            assert isinstance(inputs, dict)
            inputs[str(values["name"])] = values.get("value") or ""
        elif self._form is not None and tag == "button":
            self._button = []

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._form is not None and self._button is not None:
            self._form["button"] = "".join(self._button).strip()
            self._button = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


@pytest.fixture
def ui_client(db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path, password_hash: str):
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    monkeypatch.setenv("DECK_INBOX_DIR", str(tmp_path / "inbox"))
    app = create_app()

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


@pytest.fixture
def password_hash() -> str:
    return PasswordHasher().hash(PASSWORD)


def login(client: TestClient) -> str:
    page = client.get("/ui/login")
    assert page.status_code == 200
    csrf = Inputs(page.text).inputs["csrf"]
    response = client.post(
        "/ui/login", data={"csrf": csrf, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client.cookies[CSRF_COOKIE]


def _turn(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "channel_turn.v1",
        "channel": "deck_ui",
        "account": "owner",
        "external_thread_id": "ui-memory-1",
        "external_message_id": f"m-{uuid.uuid4().hex}",
        "role": "owner",
        "text": SENTENCE,
        "occurred_at": "2026-11-10T08:00:00Z",
        "data_class": "local_only",
        "ingest_method": "deck_ui",
        "source_ref": {"ref": "deck_ui:ui-memory-1"},
    }
    base.update(overrides)
    return base


def _click(forms: Forms, *, button: str, target: str) -> dict[str, str]:
    for form in forms.forms:
        inputs = form["inputs"]
        assert isinstance(inputs, dict)
        if form["button"] == button and inputs.get("target") == target:
            return {str(key): str(value) for key, value in inputs.items()}
    raise AssertionError(button)


def test_quarantine_shows_full_text_chips_and_the_approval_hash(
    ui_client: TestClient, db_session: Session
) -> None:
    login(ui_client)
    outcome = ingest_turn(db_session, _turn())
    db_session.flush()
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    expected = shown_sha256(message_shown_bytes(message, decision="approved", mark_proven=False))
    page = ui_client.get("/ui/memory")
    assert page.status_code == 200
    assert SENTENCE in page.text
    assert "Kanal" in page.text and "deck_ui" in page.text
    assert "Konto" in page.text and "owner" in page.text
    assert "Methode" in page.text and "Herkunft" in page.text and "Nachweis" in page.text
    assert expected in page.text
    assert '<option value="cloud_ok">' in page.text
    posted = _click(Forms(page.text), button="Freigeben", target=str(message.id))
    approved = ui_client.post("/ui/memory/act", data=posted, follow_redirects=False)
    assert approved.status_code == 303
    db_session.expire_all()
    stored = db_session.get(AssistantMessage, message.id)
    assert stored is not None and stored.state == "approved"
    again = ui_client.get("/ui/memory")
    assert SENTENCE not in again.text.split('id="quarantaene"', 1)[-1].split('id="fakten"', 1)[0]


def test_wrong_shown_hash_writes_no_approval(ui_client: TestClient, db_session: Session) -> None:
    login(ui_client)
    outcome = ingest_turn(db_session, _turn())
    db_session.flush()
    page = ui_client.get("/ui/memory")
    posted = _click(Forms(page.text), button="Freigeben", target=str(outcome.message_id))
    posted["shown_sha256"] = "ab" * 32
    before = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    rejected = ui_client.post("/ui/memory/act", data=posted)
    assert rejected.status_code == 422
    assert "shown_bytes_mismatch" in rejected.text
    db_session.expire_all()
    stored = db_session.get(AssistantMessage, outcome.message_id)
    assert stored is not None and stored.state == "quarantined"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == before


def test_clear_and_decision_change_are_two_corrections(
    ui_client: TestClient, db_session: Session
) -> None:
    login(ui_client)
    token = ui_client.cookies[SESSION_COOKIE]
    outcome = ingest_turn(db_session, _turn())
    assert outcome.message_id is not None
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    decide_message(
        db_session,
        message.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="approved")),
    )
    fact = propose_fact(
        db_session,
        source_message_id=message.id,
        original_sentence="The kettle is blue.",
        subject="kettle",
        predicate="is",
        object_="blue",
    )
    decide_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
    )
    moment = datetime(2026, 1, 2, tzinfo=UTC)
    correct_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(
            fact_shown_bytes(
                fact,
                decision="approved",
                corrects=fact.approval_id,
                valid_to=moment,
            )
        ),
        valid_to=moment,
    )
    db_session.flush()
    before = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    page = ui_client.get("/ui/memory")
    cleared = ui_client.post(
        "/ui/memory/act",
        data=_click(Forms(page.text), button="valid_to leeren", target=str(fact.id)),
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    db_session.expire_all()
    stored = db_session.get(MemoryFact, fact.id)
    assert stored is not None
    assert stored.valid_to is None and stored.status == "approved"
    after_clear = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    assert after_clear == before + 1
    page = ui_client.get("/ui/memory")
    changed = ui_client.post(
        "/ui/memory/act",
        data=_click(Forms(page.text), button="Auf abgelehnt korrigieren", target=str(fact.id)),
        follow_redirects=False,
    )
    assert changed.status_code == 303
    db_session.expire_all()
    stored = db_session.get(MemoryFact, fact.id)
    assert stored is not None and stored.status == "rejected" and stored.valid_to is None
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == after_clear + 1


def test_clear_of_an_approved_fact_rejects_a_rejected_source(
    ui_client: TestClient, db_session: Session
) -> None:
    """source_rejected on clear_fact_bounds writes nothing and the page shows the code."""
    login(ui_client)
    token = ui_client.cookies[SESSION_COOKIE]
    outcome = ingest_turn(db_session, _turn())
    assert outcome.message_id is not None
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    decide_message(
        db_session,
        message.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="approved")),
    )
    fact = propose_fact(
        db_session,
        source_message_id=message.id,
        original_sentence="The kettle is blue.",
        subject="kettle",
        predicate="is",
        object_="blue",
    )
    decide_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
    )
    moment = datetime(2026, 1, 2, tzinfo=UTC)
    correct_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(
            fact_shown_bytes(fact, decision="approved", corrects=fact.approval_id, valid_to=moment)
        ),
        valid_to=moment,
    )
    db_session.refresh(message)
    correct_message(
        db_session,
        message.id,
        decision="rejected",
        session_token=token,
        shown_sha256_hex=shown_sha256(
            message_shown_bytes(
                message,
                decision="rejected",
                corrects=message.approval_id,
            )
        ),
    )
    db_session.flush()
    before = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    page = ui_client.get("/ui/memory")
    refused = ui_client.post(
        "/ui/memory/act",
        data=_click(Forms(page.text), button="valid_to leeren", target=str(fact.id)),
    )
    assert refused.status_code == 422
    assert "source_rejected" in refused.text
    db_session.expire_all()
    stored = db_session.get(MemoryFact, fact.id)
    assert stored is not None and stored.valid_to == moment and stored.status == "approved"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == before


def _reply(db_session: Session, *, text: str, data_class: str = "local_only") -> AssistantMessage:
    outcome = ingest_turn(db_session, _turn(text=text, data_class=data_class))
    assert outcome.message_id is not None
    stored = record_deck_reply(db_session, inbound_message_id=outcome.message_id, text=text)
    assert stored.message_id is not None
    message = db_session.get(AssistantMessage, stored.message_id)
    assert message is not None
    return message


def test_persist_approves_only_the_displayed_deck_answer(
    ui_client: TestClient, db_session: Session
) -> None:
    """F5: persist pre-approves an unmarked displayed answer. The owner line stays."""
    login(ui_client)
    page = ui_client.get("/ui/assistant")
    csrf = Inputs(page.text).inputs["csrf"]
    saved = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": csrf,
            "message": "Wie geht es dir heute Morgen",
            "data_class": "local_only",
            "persist": "yes",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert "Gespeichert, vom Owner gewählt" in saved.text
    assert db_session.scalar(select(func.count()).select_from(AssistantTurn)) == 1
    db_session.expire_all()
    rows = list(db_session.scalars(select(AssistantMessage)))
    owner = next(row for row in rows if row.role == "owner")
    answer = next(row for row in rows if row.role == "assistant")
    assert owner.state == "quarantined" and owner.privacy_hold is None
    assert answer.state == "approved" and answer.privacy_hold is None


def test_persist_leaves_a_held_answer_in_quarantine(
    ui_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A marked answer is not pre-approved. The inbox still shows the hold."""
    path = tmp_path / "blocklist.txt"
    path.write_text("zucchiniplan\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    login(ui_client)
    page = ui_client.get("/ui/assistant")
    csrf = Inputs(page.text).inputs["csrf"]
    saved = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": csrf,
            "message": "Wie geht es dir heute Morgen zucchiniplan",
            "data_class": "cloud_ok",
            "persist": "yes",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    db_session.expire_all()
    rows = list(db_session.scalars(select(AssistantMessage)))
    owner = next(row for row in rows if row.role == "owner")
    answer = next(row for row in rows if row.role == "assistant")
    assert owner.state == "quarantined" and owner.privacy_hold == "privacy_block"
    assert answer.state == "quarantined" and answer.privacy_hold == "privacy_block"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == 0
    inbox = ui_client.get("/ui/memory")
    quarantine = inbox.text.split('id="quarantaene"', 1)[-1].split('id="fakten"', 1)[0]
    assert answer.text in quarantine
    assert "Markierte Quarantäne" in quarantine
    assert "Datenschutz-Treffer" in quarantine


def test_persist_path_rejects_a_wrong_hash_for_a_non_reply(
    ui_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong hash on the persist release of a non-reply is ``not_found``.

    ``conduct_dialogue`` is pointed at the owner line and given a digest that does
    not match that line. The assistant-role check refuses it before the hash
    comparison. Dropping that check lets ``decide_message`` raise
    ``shown_bytes_mismatch`` instead.
    """
    login(ui_client)
    token = ui_client.cookies[SESSION_COOKIE]

    def owner_instead(
        session: Session, *, inbound_message_id: uuid.UUID, text: str
    ) -> IngestOutcome:
        outcome = record_deck_reply(session, inbound_message_id=inbound_message_id, text=text)
        assert outcome.status == "accepted"
        return IngestOutcome(
            status="accepted",
            message_id=inbound_message_id,
            thread_id=outcome.thread_id,
            privacy_hold=outcome.privacy_hold,
        )

    monkeypatch.setattr("app.assistant.memory_dialogue.record_deck_reply", owner_instead)
    monkeypatch.setattr("app.assistant.memory_dialogue.shown_sha256", lambda _payload: "ab" * 32)
    before = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    with pytest.raises(ConversationError) as refused:
        conduct_dialogue(
            db_session,
            message="Wie geht es dir heute Morgen",
            data_class="local_only",
            include_profile=False,
            include_history=False,
            external_thread_id="",
            persist=True,
            session_token=token,
        )
    assert refused.value.code == "not_found"
    db_session.expire_all()
    rows = list(db_session.scalars(select(AssistantMessage)))
    assert rows and {row.state for row in rows} == {"quarantined"}
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == before


def test_talk_without_persist_stays_in_quarantine(
    ui_client: TestClient, db_session: Session
) -> None:
    login(ui_client)
    page = ui_client.get("/ui/assistant")
    csrf = Inputs(page.text).inputs["csrf"]
    shown = ui_client.post(
        "/ui/assistant/talk",
        data={
            "csrf": csrf,
            "message": "Wie geht es dir heute Morgen",
            "data_class": "local_only",
        },
        follow_redirects=True,
    )
    assert shown.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AssistantTurn)) == 0
    db_session.expire_all()
    rows = list(db_session.scalars(select(AssistantMessage)))
    assert {row.role for row in rows} == {"owner", "assistant"}
    assert {row.state for row in rows} == {"quarantined"}


def test_wrong_or_stale_hash_does_not_release_the_displayed_answer(
    ui_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "blocklist.txt"
    path.write_text("zucchiniplan\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    login(ui_client)
    token = ui_client.cookies[SESSION_COOKIE]
    message = _reply(db_session, text="Deck answer about zucchiniplan.", data_class="cloud_ok")
    db_session.flush()
    assert message.privacy_hold == "privacy_block" and message.state == "quarantined"
    before = db_session.scalar(select(func.count()).select_from(MemoryApproval))
    stale = shown_sha256(message_shown_bytes(message, decision="approved", mark_proven=True))
    for digest in ("ab" * 32, stale):
        with pytest.raises(ConversationError) as refused:
            approve_displayed_reply(
                db_session,
                message.id,
                session_token=token,
                shown_sha256_hex=digest,
            )
        assert refused.value.code == "shown_bytes_mismatch"
    db_session.expire_all()
    stored = db_session.get(AssistantMessage, message.id)
    assert stored is not None
    assert stored.state == "quarantined" and stored.privacy_hold == "privacy_block"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == before


def test_old_quarantine_row_stays_visible_behind_newer_approvals(
    ui_client: TestClient, db_session: Session
) -> None:
    login(ui_client)
    token = ui_client.cookies[SESSION_COOKIE]
    old = ingest_turn(db_session, _turn(text="OLDQUARANTINE stays visible."))
    assert old.message_id is not None
    for index in range(80):
        outcome = ingest_turn(
            db_session, _turn(text=f"Approved newer line {index}.", data_class="local_only")
        )
        assert outcome.message_id is not None
        message = db_session.get(AssistantMessage, outcome.message_id)
        assert message is not None
        decide_message(
            db_session,
            message.id,
            decision="approved",
            session_token=token,
            shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="approved")),
        )
    db_session.flush()
    page = ui_client.get("/ui/memory")
    quarantine = page.text.split('id="quarantaene"', 1)[-1].split('id="fakten"', 1)[0]
    assert "OLDQUARANTINE stays visible." in quarantine
    assert "Approved newer line 0." not in quarantine


def test_quarantine_cap_reports_more_open_rows(ui_client: TestClient, db_session: Session) -> None:
    login(ui_client)
    for index in range(31):
        ingest_turn(db_session, _turn(text=f"Open line {index}."))
    db_session.flush()
    page = ui_client.get("/ui/memory")
    quarantine = page.text.split('id="quarantaene"', 1)[-1].split('id="fakten"', 1)[0]
    assert "weitere vorhanden" in quarantine
    assert "Open line 30." in quarantine
    assert "Open line 0." not in quarantine


def test_conversation_audit_is_separate_from_all(
    ui_client: TestClient, db_session: Session
) -> None:
    login(ui_client)
    ingest_turn(db_session, _turn())
    db_session.flush()
    conversation = ui_client.get("/ui/audit?source=conversation")
    assert conversation.status_code == 200
    assert "assistant.turn_accepted" in conversation.text
    assert SENTENCE not in conversation.text
    everything = ui_client.get("/ui/audit")
    assert "assistant.turn_accepted" not in everything.text
    assert "Gespräch" in everything.text
