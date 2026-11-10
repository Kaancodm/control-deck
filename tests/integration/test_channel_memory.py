"""Ingest, quarantine, owner approval, memory context, and backfill on the test DB."""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.assistant.dialogue import write_profile
from app.assistant.records import AssistantTurn
from app.audit.presentation import AuditFilter
from app.audit.repository import list_audit
from app.db.models import Event
from app.db.session import get_engine
from app.db.ui_auth import UiSession
from app.memory.approval import (
    approver_reference,
    create_owner_thread,
    decide_fact,
    decide_message,
    decide_thread_link,
    decide_thread_topic,
    fact_shown_bytes,
    link_shown_bytes,
    message_shown_bytes,
    propose_fact,
    propose_thread_topic,
    shown_sha256,
    thread_shown_bytes,
)
from app.memory.backfill import COLLECTION_THREAD_ID, backfill_assistant_turns
from app.memory.context import load_memory_context
from app.memory.ingest import ingest_turn, record_deck_reply
from app.memory.models import (
    AssistantMessage,
    AssistantThread,
    ChannelAccount,
    MemoryApproval,
    MemoryFact,
    ThreadLink,
)
from app.memory.policy import ConversationError
from app.ui.auth import get_auth_settings, password_fingerprint, token_digest

# Synthetic secret trap, assembled at runtime so the repository holds no token literal.
SECRET = "sk-" + "channeltrap" + "7" * 8


def _turn(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "channel_turn.v1",
        "channel": "deck_ui",
        "account": "owner",
        "external_thread_id": "ui-thread-1",
        "external_message_id": f"m-{uuid.uuid4().hex}",
        "role": "owner",
        "text": "Synthetic remark about watering the tomatoes.",
        "occurred_at": "2026-11-10T08:00:00Z",
        "data_class": "local_only",
        "ingest_method": "deck_ui",
        "source_ref": {"ref": "deck_ui:ui-thread-1"},
    }
    base.update(overrides)
    return base


def _account(
    session: Session, channel: str, label: str, classes: list[str], *, active: bool = True
) -> None:
    session.add(
        ChannelAccount(
            channel=channel, account_label=label, active=active, allowed_data_classes=classes
        )
    )
    session.flush()


_OWNER_PASSWORD_HASH = PasswordHasher().hash("synthetic-owner-password-for-memory-tests")


@pytest.fixture(autouse=True)
def _owner_password_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session checks need a configured owner password (synthetic, test-only)."""
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", _OWNER_PASSWORD_HASH)


@pytest.fixture(autouse=True)
def _blocklist_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cloud classes fail closed without a blocklist; give tests an empty one."""
    path = tmp_path / "empty-blocklist.txt"
    path.write_text("# synthetic, empty\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))


def _owner_session(
    session: Session,
    *,
    authenticated: bool = True,
    revoked: bool = False,
    expired: bool = False,
    idle: bool = False,
    fingerprint_mismatch: bool = False,
) -> str:
    """Return the raw cookie. The row stores only ``token_digest`` of that cookie."""
    now = datetime.now(UTC)
    raw = secrets.token_urlsafe(32)
    digest = token_digest(raw)
    assert digest is not None
    fingerprint = "ab" * 32 if fingerprint_mismatch else password_fingerprint(get_auth_settings())
    session.add(
        UiSession(
            token_hash=digest,
            csrf_hash=sha256(secrets.token_bytes(32)).hexdigest(),
            authenticated=authenticated,
            created_at=now - timedelta(minutes=5),
            expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
            revoked_at=now if revoked else None,
            last_active_at=now - timedelta(hours=2) if idle else now,
            password_fingerprint=fingerprint,
            client_hash=None,
        )
    )
    session.flush()
    return raw


def _approve_message(session: Session, token: str, message_id: uuid.UUID) -> AssistantMessage:
    message = session.get(AssistantMessage, message_id)
    assert message is not None
    return decide_message(
        session,
        message_id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="approved")),
    )


def _owner_thread(session: Session, token: str, title: str, data_class: str) -> AssistantThread:
    shown = thread_shown_bytes(title=title, data_class=data_class, decision="approved")
    return create_owner_thread(
        session,
        title=title,
        data_class=data_class,
        session_token=token,
        shown_sha256_hex=shown_sha256(shown),
    )


def _accept_link(
    session: Session, token: str, link_id: uuid.UUID, thread: AssistantThread
) -> ThreadLink:
    link = session.get(ThreadLink, link_id)
    assert link is not None
    return decide_thread_link(
        session,
        link_id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(link_shown_bytes(link, thread, decision="approved")),
        target_thread_id=thread.id,
    )


def _events(session: Session, event_type: str) -> list[Event]:
    return list(
        session.scalars(select(Event).where(Event.event_type == event_type).order_by(Event.id))
    )


def _messages(session: Session) -> list[AssistantMessage]:
    return list(session.scalars(select(AssistantMessage).order_by(AssistantMessage.seq)))


# --- allowlist ------------------------------------------------------------------------


def test_unknown_channel_and_unknown_or_inactive_account_are_rejected_and_audited(
    db_session: Session,
) -> None:
    unknown_channel = ingest_turn(db_session, _turn(channel="chatgpt"))
    assert unknown_channel.status == "rejected" and unknown_channel.reason == "invalid_envelope"
    grok = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account="kaan_bot",
            data_class="cloud_ok",
            ingest_method="live_push",
            source_ref={"ref": "grok:t1"},
        ),
    )
    assert grok.status == "rejected" and grok.reason == "unknown_account"
    _account(db_session, "grok", "grok1", ["cloud_ok"], active=False)
    inactive = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account="grok1",
            data_class="cloud_ok",
            ingest_method="live_push",
            source_ref={"ref": "grok:t1"},
        ),
    )
    assert inactive.status == "rejected" and inactive.reason == "account_inactive"
    assert _messages(db_session) == []
    reasons = [event.detail["reason"] for event in _events(db_session, "assistant.turn_rejected")]
    assert reasons == ["invalid_envelope", "unknown_account", "account_inactive"]
    for event in _events(db_session, "assistant.turn_rejected"):
        assert event.task_id is None and event.to_status is None


def test_seed_is_only_deck_ui_owner(db_session: Session) -> None:
    rows = db_session.scalars(select(ChannelAccount)).all()
    assert [(row.channel, row.account_label, row.active) for row in rows] == [
        ("deck_ui", "owner", True)
    ]


# --- quarantine, dedup, race ---------------------------------------------------------


def test_new_turn_is_quarantined_unmapped_and_gets_a_proposal(db_session: Session) -> None:
    outcome = ingest_turn(db_session, _turn())
    assert outcome.status == "accepted" and outcome.reason == "thread_unmapped"
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    assert message.state == "quarantined" and message.thread_id is None
    assert message.evidence_status == "unbewiesen" and message.approval_id is None
    assert message.method == "deck_ui" and message.source_ref == "deck_ui:ui-thread-1"
    link = db_session.get(ThreadLink, outcome.link_id)
    assert link is not None and link.state == "proposed"
    proposed = db_session.get(AssistantThread, link.thread_id)
    assert proposed is not None
    assert proposed.status == "proposed" and proposed.title_origin == "assistant_proposed"
    # A second turn on the same external thread reuses the open proposal.
    again = ingest_turn(db_session, _turn(text="Another synthetic remark."))
    assert again.link_id == outcome.link_id
    assert db_session.scalar(select(func.count()).select_from(ThreadLink)) == 1


def test_dedup_by_message_id_and_by_fallback_key(db_session: Session) -> None:
    payload = _turn(external_message_id="fixed-1")
    first = ingest_turn(db_session, payload)
    second = ingest_turn(db_session, payload)
    assert first.status == "accepted"
    assert second.status == "duplicate" and second.message_id == first.message_id
    fallback = _turn(external_message_id=None, text="Fallback synthetic line.")
    assert ingest_turn(db_session, fallback).status == "accepted"
    assert ingest_turn(db_session, fallback).status == "duplicate"
    later = _turn(
        external_message_id=None,
        text="Fallback synthetic line.",
        occurred_at="2026-11-10T09:00:00Z",
    )
    assert ingest_turn(db_session, later).status == "accepted"
    assert len(_messages(db_session)) == 3
    duplicates = _events(db_session, "assistant.turn_duplicate")
    assert len(duplicates) == 2
    assert duplicates[0].detail["existing_message_id"] == str(first.message_id)


def test_concurrent_ingest_of_the_same_key_stores_one_row(db_session: Session) -> None:
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    payload = _turn(external_message_id="race-1")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                result = ingest_turn(session, payload)
                session.commit()
                outcomes.append(result.status)
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == []
    assert sorted(outcomes) == ["accepted", "duplicate"]
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(AssistantMessage)) == 1
    assert db_session.scalar(select(func.count()).select_from(ThreadLink)) == 1
    assert db_session.scalar(select(func.count()).select_from(AssistantThread)) == 1


# --- secrets -------------------------------------------------------------------------


def _tables_containing(session: Session, needle: str) -> list[str]:
    tables = session.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    ).all()
    hits = []
    for table in tables:
        count = session.scalar(
            text(f'SELECT count(*) FROM "{table}" AS row WHERE row::text LIKE :needle'),
            {"needle": f"%{needle}%"},
        )
        if count:
            hits.append(table)
    return hits


def test_secret_is_never_stored_anywhere(db_session: Session) -> None:
    body = f"Please keep {SECRET} for the deploy."
    outcome = ingest_turn(db_session, _turn(text=body))
    assert outcome.status == "rejected" and outcome.reason == "secret_rejected"
    rejected = _events(db_session, "assistant.turn_rejected")
    assert rejected[-1].detail["text_sha256"] == sha256(body.encode()).hexdigest()
    # Secret in the external ids or the reply path is refused too.
    _account(db_session, "grok", "kaan_bot", ["cloud_ok"])
    in_ref = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account="kaan_bot",
            data_class="cloud_ok",
            ingest_method="live_push",
            external_thread_id=SECRET,
            source_ref={"ref": "grok:t1"},
        ),
    )
    assert in_ref.reason == "secret_rejected"
    inbound = ingest_turn(db_session, _turn())
    assert inbound.message_id is not None
    reply = record_deck_reply(
        db_session, inbound_message_id=inbound.message_id, text=f"Use {SECRET}"
    )
    assert reply.status == "rejected" and reply.reason == "secret_rejected"
    token = _owner_session(db_session)
    with pytest.raises(ConversationError, match="secret_rejected"):
        propose_fact(
            db_session,
            source_message_id=inbound.message_id,
            original_sentence=f"The key is {SECRET}.",
            subject="deploy",
            predicate="uses key",
            object_="redacted",
        )
    with pytest.raises(ConversationError, match="secret_rejected"):
        propose_fact(
            db_session,
            source_message_id=inbound.message_id,
            original_sentence="  Spare note.  ",
            subject="  " + SECRET + "  ",
            predicate="holds",
            object_="nothing",
        )
    with pytest.raises(ConversationError, match="secret_rejected"):
        _owner_thread(db_session, token, f"Topic {SECRET}", "local_only")
    db_session.flush()
    assert _tables_containing(db_session, "channeltrap") == []
    assert _tables_containing(db_session, "Synthetic remark") == ["assistant_messages"]


# --- data class gate -------------------------------------------------------------------


def test_cloud_channels_refuse_local_only_and_claude_code_is_forced_local(
    db_session: Session,
) -> None:
    _account(db_session, "grok", "kaan_bot", ["cloud_redacted", "cloud_ok"])
    blocked = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account="kaan_bot",
            data_class="local_only",
            ingest_method="live_push",
            source_ref={"ref": "grok:t1"},
        ),
    )
    assert blocked.status == "rejected" and blocked.reason == "data_class_block"
    _account(db_session, "claude_code", "laptop", ["local_only"])
    forced = ingest_turn(
        db_session,
        _turn(
            channel="claude_code",
            account="laptop",
            data_class="cloud_ok",
            ingest_method="import_file",
            source_ref={"ref": "file:session-export.jsonl", "sha256": "b" * 64},
        ),
    )
    assert forced.status == "accepted"
    stored = db_session.get(AssistantMessage, forced.message_id)
    assert stored is not None and stored.data_class == "local_only"
    assert _events(db_session, "assistant.turn_accepted")[-1].detail["class_forced"] is True
    nested = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="ck_assistant_channel_accounts_cloud_ceiling"):
        _account(db_session, "slack", "owner", ["local_only", "cloud_ok"])
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="claude_code_local"):
        _account(db_session, "claude_code", "other", ["cloud_ok"])
    nested.rollback()


def test_deck_reply_paths_are_separate_from_ingest(db_session: Session) -> None:
    refused = ingest_turn(db_session, _turn(role="assistant"))
    assert refused.status == "rejected" and refused.reason == "reply_path_required"
    wrong_method = ingest_turn(db_session, _turn(ingest_method="import_file"))
    assert wrong_method.reason == "invalid_method"


# --- replies ---------------------------------------------------------------------------


def test_exactly_one_deck_reply_per_inbound_message(db_session: Session) -> None:
    inbound = ingest_turn(db_session, _turn())
    assert inbound.message_id is not None
    first = record_deck_reply(db_session, inbound_message_id=inbound.message_id, text="Reply one.")
    second = record_deck_reply(db_session, inbound_message_id=inbound.message_id, text="Reply one.")
    assert first.status == "accepted"
    assert second.status == "duplicate" and second.reason == "duplicate"
    assert second.message_id == first.message_id
    reply = db_session.get(AssistantMessage, first.message_id)
    assert reply is not None and reply.text == "Reply one."
    assert reply.reply_for_message_id == inbound.message_id
    assert reply.state == "quarantined" and reply.role == "assistant"
    nested = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="uq_assistant_messages_reply_for"):
        db_session.add(
            AssistantMessage(
                external_thread_id="ui-thread-1",
                external_message_id="manual-second-reply",
                role="assistant",
                text="Manual second reply.",
                text_sha256=sha256(b"Manual second reply.").hexdigest(),
                data_class="local_only",
                state="quarantined",
                reply_for_message_id=inbound.message_id,
                source_date=datetime.now(UTC),
                source_channel="deck_ui",
                source_account="owner",
                source_ref="deck_ui:manual",
                method="deck_ui",
                evidence_status="unbewiesen",
            )
        )
        db_session.flush()
    nested.rollback()
    _account(db_session, "grok", "kaan_bot", ["cloud_ok"])
    grok = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account="kaan_bot",
            data_class="cloud_ok",
            ingest_method="live_push",
            source_ref={"ref": "grok:t1"},
        ),
    )
    assert grok.message_id is not None
    no_reply = record_deck_reply(db_session, inbound_message_id=grok.message_id, text="Hi.")
    assert no_reply.status == "rejected" and no_reply.reason == "reply_channel"


# --- approvals -------------------------------------------------------------------------


def test_no_approval_without_a_live_owner_session_and_matching_bytes(
    db_session: Session,
) -> None:
    outcome = ingest_turn(db_session, _turn())
    assert outcome.message_id is not None
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    good_hash = shown_sha256(message_shown_bytes(message, decision="approved", mark_proven=True))
    candidates = {
        "unknown": sha256(b"no such session").hexdigest(),
        "malformed": "not-a-digest",
        "anonymous": _owner_session(db_session, authenticated=False),
        "revoked": _owner_session(db_session, revoked=True),
        "expired": _owner_session(db_session, expired=True),
        "idle": _owner_session(db_session, idle=True),
        "fingerprint": _owner_session(db_session, fingerprint_mismatch=True),
    }
    for token in candidates.values():
        with pytest.raises(ConversationError, match="owner_session_required"):
            decide_message(
                db_session,
                message.id,
                decision="approved",
                session_token=token,
                shown_sha256_hex=good_hash,
            )
    token = _owner_session(db_session)
    with pytest.raises(ConversationError, match="shown_bytes_mismatch"):
        decide_message(
            db_session,
            message.id,
            decision="approved",
            session_token=token,
            shown_sha256_hex=sha256(b"something else").hexdigest(),
        )
    assert message.state == "quarantined"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == 0
    approved = decide_message(
        db_session,
        message.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=good_hash,
        mark_proven=True,
    )
    assert approved.state == "approved" and approved.evidence_status == "bewiesen"
    approval = db_session.get(MemoryApproval, approved.approval_id)
    assert approval is not None
    digest = token_digest(token)
    assert digest is not None
    assert approval.approver_ref == approver_reference(digest)
    assert approval.approver_ref != digest and approval.shown_sha256 == good_hash
    with pytest.raises(ConversationError, match="already_decided"):
        decide_message(
            db_session,
            message.id,
            decision="rejected",
            session_token=token,
            shown_sha256_hex=good_hash,
        )
    # The approval row is append-only.
    nested = db_session.begin_nested()
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(text("DELETE FROM memory_approvals"))
    nested.rollback()


def test_database_refuses_approved_or_proven_rows_without_approval(db_session: Session) -> None:
    outcome = ingest_turn(db_session, _turn())
    for values, constraint in (
        ({"state": "approved"}, "ck_assistant_messages_decision"),
        ({"evidence_status": "bewiesen"}, "ck_assistant_messages"),
    ):
        nested = db_session.begin_nested()
        with pytest.raises(IntegrityError, match=constraint):
            db_session.execute(
                update(AssistantMessage)
                .where(AssistantMessage.id == outcome.message_id)
                .values(**values)
            )
        nested.rollback()


def test_rejection_keeps_the_row_as_abgelehnt(db_session: Session) -> None:
    token = _owner_session(db_session)
    outcome = ingest_turn(db_session, _turn())
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    decide_message(
        db_session,
        message.id,
        decision="rejected",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="rejected")),
    )
    assert message.state == "rejected" and message.evidence_status == "abgelehnt"
    assert db_session.get(AssistantMessage, message.id) is not None


def test_topic_proposal_and_link_acceptance_attach_messages(db_session: Session) -> None:
    token = _owner_session(db_session)
    _account(db_session, "grok", "kaan_bot", ["cloud_ok"])
    grok_turn = {
        "channel": "grok",
        "account": "kaan_bot",
        "data_class": "cloud_ok",
        "ingest_method": "live_push",
        "external_thread_id": "grok-chat-7",
        "source_ref": {"ref": "grok:grok-chat-7"},
    }
    first = ingest_turn(db_session, _turn(**grok_turn))
    assert first.link_id is not None
    link = db_session.get(ThreadLink, first.link_id)
    assert link is not None
    proposed = db_session.get(AssistantThread, link.thread_id)
    assert proposed is not None
    # A proposed topic cannot receive a link before the owner accepts the topic itself.
    with pytest.raises(ConversationError, match="thread_not_active"):
        _accept_link(db_session, token, link.id, proposed)
    decide_thread_topic(
        db_session,
        proposed.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(thread_shown_bytes(proposed, decision="approved")),
    )
    pending = db_session.get(AssistantMessage, first.message_id)
    assert pending is not None and pending.thread_id is None
    accepted = _accept_link(db_session, token, link.id, proposed)
    assert accepted.state == "active"
    # synchronize_session="fetch" updates the identity map inside the same session.
    assert pending.thread_id == proposed.id
    second = ingest_turn(db_session, _turn(**grok_turn, text="Follow-up synthetic line."))
    assert second.status == "accepted" and second.thread_id == proposed.id
    assert _events(db_session, "memory.link_approved")[-1].detail["attached"] == 1
    # The assistant can only propose a topic; it stays proposed.
    topic = propose_thread_topic(db_session, title="Garden plan", data_class="cloud_ok")
    assert topic.status == "proposed" and topic.approval_id is None


def test_local_only_thread_cannot_be_linked_to_a_cloud_channel(db_session: Session) -> None:
    token = _owner_session(db_session)
    _account(db_session, "slack", "owner", ["cloud_ok"])
    outcome = ingest_turn(
        db_session,
        _turn(
            channel="slack",
            account="owner",
            data_class="cloud_ok",
            ingest_method="live_push",
            external_thread_id="C01/1700000000.0001",
            source_ref={"ref": "slack:C01/1700000000.0001"},
        ),
    )
    assert outcome.link_id is not None
    private = _owner_thread(db_session, token, "Private notes", "local_only")
    with pytest.raises(ConversationError, match="local_only_cloud_link"):
        _accept_link(db_session, token, outcome.link_id, private)
    link = db_session.get(ThreadLink, outcome.link_id)
    assert link is not None and link.state == "proposed"


def test_fact_lifecycle_copies_provenance(db_session: Session) -> None:
    token = _owner_session(db_session)
    outcome = ingest_turn(db_session, _turn())
    assert outcome.message_id is not None
    fact = propose_fact(
        db_session,
        source_message_id=outcome.message_id,
        original_sentence="Die Tomaten brauchen morgens Wasser.",
        subject="tomatoes",
        predicate="need watering",
        object_="in the morning",
    )
    assert fact.status == "proposed" and fact.evidence_status == "unbewiesen"
    assert fact.source_channel == "deck_ui" and fact.method == "deck_ui"
    assert fact.data_class == "local_only"
    with pytest.raises(ConversationError, match="owner_session_required"):
        decide_fact(
            db_session,
            fact.id,
            decision="approved",
            session_token="0" * 64,
            shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
        )
    decided = decide_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
    )
    assert decided.status == "approved" and decided.approval_id is not None


# --- memory context ------------------------------------------------------------------


def _thread_with_messages(
    session: Session, token: str, *, external: str, thread_class: str, rows: list[tuple[str, str]]
) -> tuple[AssistantThread, list[uuid.UUID]]:
    """Owner thread linked to a deck_ui external thread; ``rows`` = (class, text)."""
    thread = _owner_thread(session, token, f"Topic {external}", thread_class)
    ids = []
    for index, (data_class, body) in enumerate(rows):
        outcome = ingest_turn(
            session,
            _turn(
                external_thread_id=external,
                data_class=data_class,
                text=body,
                occurred_at=f"2026-11-10T08:{index:02d}:00Z",
                source_ref={"ref": f"deck_ui:{external}"},
            ),
        )
        assert outcome.message_id is not None
        if index == 0:
            assert outcome.link_id is not None
            _accept_link(session, token, outcome.link_id, thread)
        ids.append(outcome.message_id)
    return thread, ids


def test_context_returns_only_approved_rows_with_class_filter(db_session: Session) -> None:
    token = _owner_session(db_session)
    local_thread, local_ids = _thread_with_messages(
        db_session,
        token,
        external="ctx-local",
        thread_class="local_only",
        rows=[
            ("local_only", "Local alpha line."),
            ("cloud_ok", "Cloud beta line."),
            ("cloud_redacted", "Redacted gamma line."),
            ("cloud_ok", "Quarantined delta line."),
            ("cloud_ok", "Rejected epsilon line."),
        ],
    )
    for message_id in local_ids[:3]:
        _approve_message(db_session, token, message_id)
    rejected = db_session.get(AssistantMessage, local_ids[4])
    assert rejected is not None
    decide_message(
        db_session,
        rejected.id,
        decision="rejected",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(rejected, decision="rejected")),
    )
    cloud_thread, cloud_ids = _thread_with_messages(
        db_session,
        token,
        external="ctx-cloud",
        thread_class="cloud_ok",
        rows=[("cloud_ok", "Shared zeta line."), ("cloud_ok", "Pending eta line.")],
    )
    _approve_message(db_session, token, cloud_ids[0])

    local = load_memory_context(
        db_session, thread_id=local_thread.id, target="ollama", data_class="local_only"
    )
    texts = [item.text for item in local.items if item.kind == "history"]
    assert texts == ["Local alpha line.", "Cloud beta line.", "Redacted gamma line."]

    for target in ("claude", "claude_code", "grok"):
        cloud = load_memory_context(
            db_session, thread_id=cloud_thread.id, target=target, data_class="cloud_ok"
        )
        assert [item.text for item in cloud.items] == ["Shared zeta line."]
        assert {item.data_class for item in cloud.items} == {"cloud_ok"}
        # A local_only thread never feeds a cloud target, even its cloud_ok rows.
        hidden = load_memory_context(
            db_session, thread_id=local_thread.id, target=target, data_class="cloud_ok"
        )
        assert hidden.items == ()
    codex = load_memory_context(
        db_session, thread_id=cloud_thread.id, target="codex", data_class="cloud_ok"
    )
    assert codex.items == ()


def test_class_filter_runs_before_ranking(db_session: Session) -> None:
    token = _owner_session(db_session)
    rows = {}
    for data_class in ("local_only", "cloud_redacted", "cloud_ok"):
        outcome = ingest_turn(
            db_session,
            _turn(external_thread_id=f"facts-{data_class}", data_class=data_class),
        )
        assert outcome.message_id is not None
        rows[data_class] = outcome.message_id
    facts = {
        # The local fact is the best keyword match; it must never be ranked for cloud.
        "local_only": ("Orchard irrigation valves are new.", "orchard", "valves"),  # 3 hits
        "cloud_redacted": ("The orchard needs irrigation.", "orchard", "irrigation"),  # 2 hits
        "cloud_ok": ("Weekly reminder about irrigation.", "reminder", "weekly"),  # 1 hit
    }
    for data_class, (sentence, subject, object_) in facts.items():
        fact = propose_fact(
            db_session,
            source_message_id=rows[data_class],
            original_sentence=sentence,
            subject=subject,
            predicate="has",
            object_=object_,
        )
        decide_fact(
            db_session,
            fact.id,
            decision="approved",
            session_token=token,
            shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
        )
    cloud = load_memory_context(
        db_session,
        thread_id=None,
        target="claude",
        data_class="cloud_ok",
        query="orchard irrigation valves",
    )
    assert [item.data_class for item in cloud.items] == ["cloud_ok"]
    assert cloud.dropped == 0
    local = load_memory_context(
        db_session,
        thread_id=None,
        target="ollama",
        data_class="local_only",
        query="orchard irrigation valves",
    )
    assert [item.data_class for item in local.items] == ["local_only", "cloud_redacted", "cloud_ok"]
    assert all(item.kind == "fact" for item in local.items)
    with pytest.raises(ConversationError, match="data_class_block"):
        load_memory_context(
            db_session, thread_id=None, target="grok", data_class="local_only", query="orchard"
        )


def test_budget_drops_oldest_history_first(db_session: Session) -> None:
    token = _owner_session(db_session)
    lines = [f"Line {index} " + "x" * 60 for index in range(4)]
    thread, ids = _thread_with_messages(
        db_session,
        token,
        external="budget",
        thread_class="cloud_ok",
        rows=[("cloud_ok", line) for line in lines],
    )
    for message_id in ids:
        _approve_message(db_session, token, message_id)
    per_line = -(-len(lines[0]) * 2 // 7)  # ceil(len / 3.5)
    context = load_memory_context(
        db_session,
        thread_id=thread.id,
        target="grok",
        data_class="cloud_ok",
        budget_tokens=per_line * 2,
    )
    assert [item.text for item in context.items] == lines[2:]
    assert context.dropped == 2


def _finish(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()


def test_concurrent_fallback_key_stores_one_row(db_session: Session) -> None:
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    payload = _turn(
        external_message_id=None,
        external_thread_id="fb-race",
        text="Same fallback body.",
    )
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str | None]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                result = ingest_turn(session, payload)
                session.commit()
                outcomes.append((result.status, result.reason))
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    _finish([threading.Thread(target=worker) for _ in range(2)])
    assert errors == []
    assert sorted(item[0] for item in outcomes) == ["accepted", "duplicate"]
    assert "duplicate_body_mismatch" not in {item[1] for item in outcomes}
    db_session.expire_all()
    stored = db_session.scalars(
        select(AssistantMessage).where(AssistantMessage.external_thread_id == "fb-race")
    ).all()
    assert len(stored) == 1 and stored[0].text == "Same fallback body."


def test_concurrent_messages_share_one_thread_link(db_session: Session) -> None:
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(message_id: str) -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                result = ingest_turn(
                    session,
                    _turn(
                        external_thread_id="shared-ext",
                        external_message_id=message_id,
                        text=f"Line {message_id}",
                    ),
                )
                session.commit()
                assert result.status == "accepted"
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    _finish(
        [
            threading.Thread(target=worker, args=("race-a",)),
            threading.Thread(target=worker, args=("race-b",)),
        ]
    )
    assert errors == []
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(AssistantMessage)) == 2
    assert db_session.scalar(select(func.count()).select_from(ThreadLink)) == 1
    assert db_session.scalar(select(func.count()).select_from(AssistantThread)) == 1


def test_accept_and_ingest_both_leave_the_message_attached(db_session: Session) -> None:
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    external = "accept-ingest"
    with factory() as setup:
        token = _owner_session(setup)
        thread = _owner_thread(setup, token, "Shared topic", "cloud_ok")
        first = ingest_turn(
            setup,
            _turn(
                external_thread_id=external,
                external_message_id="first-msg",
                data_class="cloud_ok",
            ),
        )
        assert first.link_id is not None and first.message_id is not None
        link = setup.get(ThreadLink, first.link_id)
        assert link is not None
        shown = shown_sha256(link_shown_bytes(link, thread, decision="approved"))
        link_id, thread_id = link.id, thread.id
        setup.commit()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def accepter() -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                decide_thread_link(
                    session,
                    link_id,
                    decision="approved",
                    session_token=token,
                    shown_sha256_hex=shown,
                    target_thread_id=thread_id,
                )
                session.commit()
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    def ingester() -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                result = ingest_turn(
                    session,
                    _turn(
                        external_thread_id=external,
                        external_message_id="second-msg",
                        data_class="cloud_ok",
                        text="Second synthetic line.",
                    ),
                )
                session.commit()
                assert result.status == "accepted"
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    _finish([threading.Thread(target=accepter), threading.Thread(target=ingester)])
    assert errors == []
    db_session.expire_all()
    rows = db_session.scalars(
        select(AssistantMessage).where(AssistantMessage.external_thread_id == external)
    ).all()
    assert len(rows) == 2
    assert {row.thread_id for row in rows} == {thread_id}


def test_concurrent_deck_replies_keep_one_body(db_session: Session) -> None:
    factory = sessionmaker(bind=get_engine(for_tests=True), autoflush=False)
    with factory() as setup:
        inbound = ingest_turn(setup, _turn(external_message_id="reply-race"))
        assert inbound.message_id is not None
        inbound_id = inbound.message_id
        setup.commit()
    barrier = threading.Barrier(2)
    texts = ("Reply alpha.", "Reply beta.")
    errors: list[BaseException] = []

    def worker(body: str) -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=10)
                record_deck_reply(session, inbound_message_id=inbound_id, text=body)
                session.commit()
            except BaseException as exc:  # pragma: no cover - surfaced below
                session.rollback()
                errors.append(exc)

    _finish(
        [
            threading.Thread(target=worker, args=(texts[0],)),
            threading.Thread(target=worker, args=(texts[1],)),
        ]
    )
    assert errors == []
    db_session.expire_all()
    replies = db_session.scalars(
        select(AssistantMessage).where(AssistantMessage.reply_for_message_id == inbound_id)
    ).all()
    assert len(replies) == 1
    kept = replies[0].text
    other = texts[1] if kept == texts[0] else texts[0]
    assert kept in texts
    assert _tables_containing(db_session, other) == []
    detail = _events(db_session, "assistant.reply_duplicate")[-1].detail
    assert detail["reason"] == "duplicate_body_mismatch"
    assert detail["text_sha256"] == sha256(other.encode()).hexdigest()
    assert detail["existing_text_sha256"] == sha256(kept.encode()).hexdigest()
    assert "Reply" not in str(detail)


def test_rejected_source_blocks_a_new_fact_and_drops_context(db_session: Session) -> None:
    token = _owner_session(db_session)
    outcome = ingest_turn(db_session, _turn(text="Tomatoes need water in the orchard."))
    assert outcome.message_id is not None
    approved = propose_fact(
        db_session,
        source_message_id=outcome.message_id,
        original_sentence="The orchard irrigation is weekly.",
        subject="orchard",
        predicate="needs",
        object_="irrigation",
    )
    decide_fact(
        db_session,
        approved.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(approved, decision="approved")),
    )
    visible = load_memory_context(
        db_session,
        thread_id=None,
        target="ollama",
        data_class="local_only",
        query="orchard irrigation",
    )
    assert any(item.reference == str(approved.id) for item in visible.items)
    pending = propose_fact(
        db_session,
        source_message_id=outcome.message_id,
        original_sentence="A second orchard note.",
        subject="orchard",
        predicate="has",
        object_="note",
    )
    message = db_session.get(AssistantMessage, outcome.message_id)
    assert message is not None
    decide_message(
        db_session,
        message.id,
        decision="rejected",
        session_token=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message, decision="rejected")),
    )
    hidden = load_memory_context(
        db_session,
        thread_id=None,
        target="ollama",
        data_class="local_only",
        query="orchard irrigation",
    )
    assert all(item.reference != str(approved.id) for item in hidden.items)
    with pytest.raises(ConversationError, match="source_rejected"):
        propose_fact(
            db_session,
            source_message_id=outcome.message_id,
            original_sentence="A third orchard note.",
            subject="orchard",
            predicate="has",
            object_="third",
        )
    with pytest.raises(ConversationError, match="source_rejected"):
        decide_fact(
            db_session,
            pending.id,
            decision="approved",
            session_token=token,
            shown_sha256_hex=shown_sha256(fact_shown_bytes(pending, decision="approved")),
        )


def test_different_body_for_the_same_external_id_is_not_overwritten(db_session: Session) -> None:
    first_text = "First body about basil."
    second_text = "Second body about basil."
    payload = _turn(external_message_id="fixed-body", text=first_text)
    first = ingest_turn(db_session, payload)
    second = ingest_turn(db_session, {**payload, "text": second_text})
    assert first.message_id is not None
    assert second.status == "duplicate" and second.reason == "duplicate_body_mismatch"
    assert second.message_id == first.message_id
    stored = db_session.get(AssistantMessage, first.message_id)
    assert stored is not None and stored.text == first_text
    detail = _events(db_session, "assistant.turn_duplicate")[-1].detail
    assert detail["text_sha256"] == sha256(second_text.encode()).hexdigest()
    assert detail["existing_text_sha256"] == sha256(first_text.encode()).hexdigest()
    assert "basil" not in str(detail)
    assert set(detail) == {"reason", "text_sha256", "existing_text_sha256"}


def test_active_topic_refuses_a_class_it_does_not_admit(db_session: Session) -> None:
    token = _owner_session(db_session)
    thread = _owner_thread(db_session, token, "Cloud topic", "cloud_ok")
    first = ingest_turn(
        db_session,
        _turn(external_thread_id="mixed-class", data_class="cloud_ok", text="Cloud line."),
    )
    assert first.link_id is not None
    _accept_link(db_session, token, first.link_id, thread)
    blocked = ingest_turn(
        db_session,
        _turn(
            external_thread_id="mixed-class",
            data_class="local_only",
            text="Local only line.",
        ),
    )
    assert blocked.status == "rejected" and blocked.reason == "thread_class_block"
    assert _tables_containing(db_session, "Local only line.") == []
    reasons = [event.detail["reason"] for event in _events(db_session, "assistant.turn_rejected")]
    assert reasons == ["thread_class_block"]
    assert db_session.scalar(select(func.count()).select_from(AssistantMessage)) == 1


def test_secret_account_never_reaches_the_audit(db_session: Session) -> None:
    account = "g" + "hp_" + "a" * 16
    body = "Ordinary sentence about basil."
    outcome = ingest_turn(
        db_session,
        _turn(
            channel="grok",
            account=account,
            data_class="cloud_ok",
            ingest_method="live_push",
            source_ref={"ref": "grok:t1"},
            text=body,
        ),
    )
    assert outcome.status == "rejected" and outcome.reason == "secret_rejected"
    detail = _events(db_session, "assistant.turn_rejected")[-1].detail
    assert set(detail) <= {"reason", "text_sha256"}
    assert account not in str(detail)
    assert _tables_containing(db_session, account) == []
    assert db_session.scalar(select(func.count()).select_from(AssistantThread)) == 0
    _account(db_session, "deck_ui", account, ["local_only"])
    inbound = AssistantMessage(
        external_thread_id="ui-thread-1",
        external_message_id="stored-account",
        role="owner",
        text="Plain inbound.",
        text_sha256=sha256(b"Plain inbound.").hexdigest(),
        data_class="local_only",
        state="quarantined",
        source_date=datetime.now(UTC),
        source_channel="deck_ui",
        source_account=account,
        source_ref="deck_ui:ui-thread-1",
        method="deck_ui",
        evidence_status="unbewiesen",
    )
    db_session.add(inbound)
    db_session.flush()
    reply = record_deck_reply(db_session, inbound_message_id=inbound.id, text="A normal reply.")
    assert reply.status == "rejected" and reply.reason == "secret_rejected"
    for event in db_session.scalars(select(Event)):
        assert account not in str(event.detail)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AssistantMessage)
            .where(AssistantMessage.role == "assistant")
        )
        == 0
    )


def test_scheme_and_reserved_reply_prefix_are_rejected(db_session: Session) -> None:
    scheme = ingest_turn(db_session, _turn(external_thread_id="note://1"))
    prefixed = ingest_turn(db_session, _turn(external_message_id="deck-reply:minted"))
    assert scheme.reason == "invalid_envelope" and prefixed.reason == "invalid_envelope"
    assert _messages(db_session) == []
    nested = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="ck_assistant_messages_external_scheme"):
        db_session.add(
            AssistantMessage(
                external_thread_id="note://1",
                external_message_id="manual-scheme",
                role="owner",
                text="Scheme row.",
                text_sha256=sha256(b"Scheme row.").hexdigest(),
                data_class="local_only",
                state="quarantined",
                source_date=datetime.now(UTC),
                source_channel="deck_ui",
                source_account="owner",
                source_ref="deck_ui:ui-thread-1",
                method="deck_ui",
                evidence_status="unbewiesen",
            )
        )
        db_session.flush()
    nested.rollback()


def test_content_triggers_block_text_updates_and_deletes(db_session: Session) -> None:
    token = _owner_session(db_session)
    outcome = ingest_turn(db_session, _turn())
    assert outcome.message_id is not None and outcome.link_id is not None
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(
            update(AssistantMessage)
            .where(AssistantMessage.id == outcome.message_id)
            .values(text="Changed body.")
        )
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(
            text("DELETE FROM assistant_messages WHERE id = :id"), {"id": outcome.message_id}
        )
    nested.rollback()
    thread = _owner_thread(db_session, token, "Attach target", "local_only")
    db_session.execute(
        update(AssistantMessage)
        .where(AssistantMessage.id == outcome.message_id)
        .values(thread_id=thread.id)
    )
    db_session.flush()
    stored = db_session.get(AssistantMessage, outcome.message_id)
    assert stored is not None and stored.thread_id == thread.id
    assert stored.text == "Synthetic remark about watering the tomatoes."
    fact = propose_fact(
        db_session,
        source_message_id=outcome.message_id,
        original_sentence="Tomatoes like morning water.",
        subject="tomatoes",
        predicate="like",
        object_="water",
    )
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(
            update(MemoryFact)
            .where(MemoryFact.id == fact.id)
            .values(original_sentence="Changed sentence.")
        )
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(text("DELETE FROM memory_facts WHERE id = :id"), {"id": fact.id})
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(
            update(AssistantThread)
            .where(AssistantThread.id == thread.id)
            .values(data_class="cloud_ok")
        )
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(text("DELETE FROM assistant_threads WHERE id = :id"), {"id": thread.id})
    nested.rollback()
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError, match="memory_content_immutable"):
        db_session.execute(
            text("DELETE FROM assistant_thread_links WHERE id = :id"), {"id": outcome.link_id}
        )
    nested.rollback()
    assert db_session.get(AssistantMessage, outcome.message_id) is not None
    assert db_session.get(MemoryFact, fact.id) is not None


def test_privacy_block_rejects_cloud_ok_and_context_drops_it(
    db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    term = "zucchiniplan"
    path = tmp_path / "blocklist.txt"
    path.write_text(term + "\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    blocked = ingest_turn(
        db_session,
        _turn(data_class="cloud_ok", text=f"Note about {term} today."),
    )
    assert blocked.status == "rejected" and blocked.reason == "privacy_block"
    assert _tables_containing(db_session, term) == []
    for event in _events(db_session, "assistant.turn_rejected"):
        assert term not in str(event.detail)
    local = ingest_turn(db_session, _turn(data_class="local_only", text=f"Local {term} note."))
    assert local.status == "accepted"
    redacted = ingest_turn(
        db_session,
        _turn(data_class="cloud_redacted", text=f"Redacted {term} note."),
    )
    assert redacted.status == "accepted"
    token = _owner_session(db_session)
    thread, ids = _thread_with_messages(
        db_session,
        token,
        external="priv-cloud",
        thread_class="cloud_ok",
        rows=[("cloud_ok", "Garden hose is coiled.")],
    )
    _approve_message(db_session, token, ids[0])
    visible = load_memory_context(
        db_session, thread_id=thread.id, target="grok", data_class="cloud_ok"
    )
    assert [item.text for item in visible.items] == ["Garden hose is coiled."]
    path.write_text("coiled\n", encoding="utf-8")
    hidden = load_memory_context(
        db_session, thread_id=thread.id, target="grok", data_class="cloud_ok"
    )
    assert hidden.items == () and hidden.dropped >= 1


def test_context_items_carry_evidence_status(db_session: Session) -> None:
    token = _owner_session(db_session)
    write_profile(db_session, "Owner typed garden note.", actor="owner")
    thread, ids = _thread_with_messages(
        db_session,
        token,
        external="evidence",
        thread_class="local_only",
        rows=[("local_only", "History line about oregano.")],
    )
    _approve_message(db_session, token, ids[0])
    fact = propose_fact(
        db_session,
        source_message_id=ids[0],
        original_sentence="Oregano likes a dry bed.",
        subject="oregano",
        predicate="likes",
        object_="dry bed",
    )
    decide_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(fact, decision="approved")),
    )
    context = load_memory_context(
        db_session,
        thread_id=thread.id,
        target="ollama",
        data_class="local_only",
        query="oregano",
    )
    by_kind = {item.kind: item.evidence_status for item in context.items}
    assert by_kind == {"profile": "owner-typed", "history": "unbewiesen", "fact": "unbewiesen"}


# --- backfill and audit views ---------------------------------------------------------


def test_backfill_copies_turns_as_quarantined_and_rerun_is_noop(db_session: Session) -> None:
    created = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    db_session.add_all(
        [
            AssistantTurn(
                data_class="local_only",
                owner_text="Synthetic owner question one.",
                reply_text="Synthetic reply one.",
                reply_origin="mock",
                created_at=created,
            ),
            AssistantTurn(
                data_class="cloud_ok",
                owner_text="Synthetic owner question two.",
                reply_text=f"Reply with {SECRET}.",
                reply_origin="mock",
                created_at=created + timedelta(minutes=1),
            ),
        ]
    )
    db_session.flush()
    report = backfill_assistant_turns(db_session)
    assert (report.inserted, report.duplicates, report.secret_rejected) == (3, 0, 1)
    assert report.thread_id == COLLECTION_THREAD_ID
    rows = _messages(db_session)
    assert {row.state for row in rows} == {"quarantined"}
    assert {(row.source_channel, row.source_account, row.method) for row in rows} == {
        ("deck_ui", "owner", "deck_ui")
    }
    assert {row.thread_id for row in rows} == {COLLECTION_THREAD_ID}
    reply = next(row for row in rows if row.role == "assistant")
    assert reply.reply_for_message_id is not None
    events_before = db_session.scalar(select(func.count()).select_from(Event))
    again = backfill_assistant_turns(db_session)
    assert (again.inserted, again.duplicates) == (0, 3)
    assert len(_messages(db_session)) == 3
    assert db_session.scalar(select(func.count()).select_from(Event)) == events_before
    assert db_session.scalar(select(func.count()).select_from(AssistantTurn)) == 2
    assert _tables_containing(db_session, "channeltrap") == ["assistant_turns"]


def test_task_audit_views_ignore_conversation_rows(db_session: Session) -> None:
    ingest_turn(db_session, _turn())
    ingest_turn(db_session, _turn(channel="chatgpt"))
    db_session.commit()
    page = list_audit(db_session, AuditFilter(source="task"))
    assert all(item.task_id is not None for item in page.items)
    assert page.total == 0
    everything = list_audit(db_session, AuditFilter())
    conversation = ("assistant.", "memory.")
    assert all(not item.event_type.startswith(conversation) for item in everything.items)
    assert db_session.scalar(select(func.count()).select_from(Event)) == 2


def test_decided_rows_are_frozen_and_thread_attach_is_one_way(db_session: Session) -> None:
    token = _owner_session(db_session)
    thread, ids = _thread_with_messages(
        db_session,
        token,
        external="freeze-1",
        thread_class="local_only",
        rows=[("local_only", "Synthetic note about the shed key.")],
    )
    _approve_message(db_session, token, ids[0])
    other = _owner_thread(db_session, token, "Other topic", "local_only")
    for values in (
        {"state": "rejected"},
        {"evidence_status": "bewiesen"},
        {"approval_id": None},
        {"thread_id": other.id},
    ):
        nested = db_session.begin_nested()
        with pytest.raises(DBAPIError, match="memory_content_immutable"):
            db_session.execute(
                update(AssistantMessage).where(AssistantMessage.id == ids[0]).values(**values)
            )
        nested.rollback()
    stored = db_session.get(AssistantMessage, ids[0])
    assert stored is not None
    db_session.refresh(stored)
    assert stored.state == "approved" and stored.thread_id == thread.id


def test_missing_blocklist_fails_closed_for_cloud_but_keeps_local_context(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _owner_session(db_session)
    thread, ids = _thread_with_messages(
        db_session,
        token,
        external="noblock-1",
        thread_class="cloud_ok",
        rows=[("cloud_ok", "Synthetic note: the rake hangs left.")],
    )
    _approve_message(db_session, token, ids[0])
    monkeypatch.delenv("DECK_BLOCKLIST_PATH", raising=False)
    refused = ingest_turn(
        db_session, _turn(data_class="cloud_ok", text="Another synthetic cloud note.")
    )
    assert refused.status == "rejected" and refused.reason == "privacy_block"
    local = ingest_turn(db_session, _turn(data_class="local_only", text="Local synthetic note."))
    assert local.status == "accepted"
    cloud = load_memory_context(
        db_session, thread_id=thread.id, target="claude", data_class="cloud_ok"
    )
    assert cloud.items == () and cloud.dropped >= 1
    on_box = load_memory_context(
        db_session, thread_id=thread.id, target="ollama", data_class="cloud_ok"
    )
    assert [item.text for item in on_box.items] == ["Synthetic note: the rake hangs left."]


def test_propose_fact_applies_the_privacy_block(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    term = "pumpkinledger"
    token = _owner_session(db_session)
    _thread, ids = _thread_with_messages(
        db_session,
        token,
        external="factpriv-1",
        thread_class="cloud_ok",
        rows=[("cloud_ok", "Synthetic note about squash.")],
    )
    path = tmp_path / "fact-blocklist.txt"
    path.write_text(term + "\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    with pytest.raises(ConversationError, match="privacy_block"):
        propose_fact(
            db_session,
            source_message_id=ids[0],
            original_sentence=f"Squash goes in the {term}.",
            subject="squash",
            predicate="goes_in",
            object_=term,
        )
    assert _tables_containing(db_session, term) == []
