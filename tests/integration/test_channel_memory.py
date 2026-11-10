"""Ingest, quarantine, owner approval, memory context, and backfill on the test DB."""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.assistant.records import AssistantTurn
from app.audit.presentation import AuditFilter
from app.audit.repository import list_audit
from app.db.models import Event
from app.db.session import get_engine
from app.db.ui_auth import UiSession
from app.memory.approval import (
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
    ThreadLink,
)
from app.memory.policy import ConversationError

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


def _owner_session(
    session: Session,
    *,
    authenticated: bool = True,
    revoked: bool = False,
    expired: bool = False,
) -> str:
    now = datetime.now(UTC)
    token_hash = sha256(secrets.token_bytes(32)).hexdigest()
    session.add(
        UiSession(
            token_hash=token_hash,
            csrf_hash=sha256(secrets.token_bytes(32)).hexdigest(),
            authenticated=authenticated,
            created_at=now - timedelta(minutes=5),
            expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
            revoked_at=now if revoked else None,
            last_active_at=now,
            password_fingerprint=None,
            client_hash=None,
        )
    )
    session.flush()
    return token_hash


def _approve_message(session: Session, token: str, message_id: uuid.UUID) -> AssistantMessage:
    message = session.get(AssistantMessage, message_id)
    assert message is not None
    return decide_message(
        session,
        message_id,
        decision="approved",
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message)),
    )


def _owner_thread(session: Session, token: str, title: str, data_class: str) -> AssistantThread:
    return create_owner_thread(
        session,
        title=title,
        data_class=data_class,
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(title.encode("utf-8")),
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
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(link_shown_bytes(link, thread)),
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
    second = record_deck_reply(db_session, inbound_message_id=inbound.message_id, text="Reply two.")
    assert first.status == "accepted"
    assert second.status == "duplicate" and second.message_id == first.message_id
    reply = db_session.get(AssistantMessage, first.message_id)
    assert reply is not None and reply.reply_for_message_id == inbound.message_id
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
    good_hash = shown_sha256(message_shown_bytes(message))
    candidates = {
        "unknown": sha256(b"no such session").hexdigest(),
        "malformed": "not-a-digest",
        "anonymous": _owner_session(db_session, authenticated=False),
        "revoked": _owner_session(db_session, revoked=True),
        "expired": _owner_session(db_session, expired=True),
    }
    for token in candidates.values():
        with pytest.raises(ConversationError, match="owner_session_required"):
            decide_message(
                db_session,
                message.id,
                decision="approved",
                session_token_hash=token,
                shown_sha256_hex=good_hash,
            )
    token = _owner_session(db_session)
    with pytest.raises(ConversationError, match="shown_bytes_mismatch"):
        decide_message(
            db_session,
            message.id,
            decision="approved",
            session_token_hash=token,
            shown_sha256_hex=sha256(b"something else").hexdigest(),
        )
    assert message.state == "quarantined"
    assert db_session.scalar(select(func.count()).select_from(MemoryApproval)) == 0
    approved = decide_message(
        db_session,
        message.id,
        decision="approved",
        session_token_hash=token,
        shown_sha256_hex=good_hash,
        mark_proven=True,
    )
    assert approved.state == "approved" and approved.evidence_status == "bewiesen"
    approval = db_session.get(MemoryApproval, approved.approval_id)
    assert approval is not None
    assert approval.session_token_hash == token and approval.shown_sha256 == good_hash
    with pytest.raises(ConversationError, match="already_decided"):
        decide_message(
            db_session,
            message.id,
            decision="rejected",
            session_token_hash=token,
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
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(message)),
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
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(thread_shown_bytes(proposed)),
    )
    accepted = _accept_link(db_session, token, link.id, proposed)
    assert accepted.state == "active"
    attached = db_session.get(AssistantMessage, first.message_id)
    assert attached is not None and attached.thread_id == proposed.id
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
            session_token_hash="0" * 64,
            shown_sha256_hex=shown_sha256(fact_shown_bytes(fact)),
        )
    decided = decide_fact(
        db_session,
        fact.id,
        decision="approved",
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(fact_shown_bytes(fact)),
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
        session_token_hash=token,
        shown_sha256_hex=shown_sha256(message_shown_bytes(rejected)),
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
            session_token_hash=token,
            shown_sha256_hex=shown_sha256(fact_shown_bytes(fact)),
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
