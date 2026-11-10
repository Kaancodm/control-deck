"""channel_turn.v1 contract, channel policy, context targets, and static guarantees."""

from __future__ import annotations

import ast
import copy
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.memory.approval import (
    approver_reference,
    message_shown_bytes,
    shown_sha256,
    thread_shown_bytes,
)
from app.memory.audit import record_conversation_event
from app.memory.context import (
    context_classes,
    load_memory_context,
    render_fact_text,
    target_classes,
)
from app.memory.envelope import ChannelTurnV1, json_schema_valid, parse_turn
from app.memory.ingest import contains_secret
from app.memory.models import AssistantMessage
from app.memory.policy import (
    CHANNEL_CLASS_CEILING,
    CLOUD_CHANNELS,
    ConversationError,
    check_link_class,
)
from app.privacy.gate import MEMORY_HOLD_CODES, memory_privacy_hold, strongest_hold

MEMORY = Path("app/memory")


def _turn(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "channel_turn.v1",
        "channel": "grok",
        "account": "kaan_bot",
        "external_thread_id": "thread-001",
        "external_message_id": "msg-001",
        "role": "owner",
        "text": "Synthetic note about the garden plan.",
        "occurred_at": "2026-11-10T08:00:00Z",
        "data_class": "cloud_ok",
        "ingest_method": "live_push",
        "source_ref": {"ref": "grok:thread-001/msg-001", "sha256": None},
    }
    base.update(overrides)
    return base


def _pydantic_ok(payload: dict[str, object]) -> bool:
    try:
        ChannelTurnV1.model_validate(payload)
    except ValidationError:
        return False
    return True


def test_schema_copies_are_identical() -> None:
    root = Path("schemas/channel_turn.v1.json").read_bytes()
    packaged = Path("app/schemas/channel_turn.v1.json").read_bytes()
    assert root == packaged


def _without(key: str) -> dict[str, object]:
    payload = _turn()
    del payload[key]
    return payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_turn(), True),
        (_turn(external_message_id=None), True),
        (_turn(in_reply_to="msg-000"), True),
        (_turn(source_ref={"ref": "file:export-2026-11.json", "sha256": "a" * 64}), True),
        (_turn(occurred_at="2026-11-10T08:00:00.123+01:00"), True),
        (_without("data_class"), False),
        (_without("external_message_id"), False),
        (_turn(data_class="internal"), False),
        (_turn(channel="chatgpt"), False),
        (_turn(channel="codex"), False),
        (_turn(account="Kaan Bot"), False),
        (_turn(role="system"), False),
        (_turn(text=""), False),
        (_turn(occurred_at="2026-11-10T08:00:00"), False),
        (_turn(source_ref={"ref": "https://example.invalid/x"}), False),
        (_turn(source_ref={"ref": "file:host:8080/x"}), False),
        (_turn(source_ref={"ref": "nowhere:x"}), False),
        (_turn(external_thread_id="note://1"), False),
        (_turn(external_message_id="note://msg"), False),
        (_turn(external_message_id="deck-reply:minted"), False),
        (_turn(in_reply_to="note://1"), False),
        (_turn(in_reply_to="deck-reply:minted"), True),
        (_turn(external_thread_id="slack:C01/1.0001"), True),
        (_turn(extra="field"), False),
        (_turn(ingest_method="scrape"), False),
    ],
)
def test_json_schema_and_pydantic_agree(payload: dict[str, object], expected: bool) -> None:
    assert json_schema_valid(payload) is expected
    assert _pydantic_ok(payload) is expected


def test_parse_turn_requires_both_validators() -> None:
    assert parse_turn(_turn()).channel == "grok"
    with pytest.raises(ConversationError, match="invalid_envelope"):
        parse_turn(_without("data_class"))
    with pytest.raises(ConversationError, match="invalid_envelope"):
        parse_turn(_turn(occurred_at="2026-13-40T08:00:00Z"))


def test_channel_policy_encodes_owner_decisions() -> None:
    # 7.1: grok and n8n are separate channels.
    assert {"grok", "n8n"} <= set(CHANNEL_CLASS_CEILING)
    # 7.7: cloud channels never carry local_only; claude_code is forced local_only.
    for channel in ("slack", "grok", "claude", "n8n"):
        assert "local_only" not in CHANNEL_CLASS_CEILING[channel]
        assert channel in CLOUD_CHANNELS
    assert CHANNEL_CLASS_CEILING["claude_code"] == ("local_only",)
    assert set(CHANNEL_CLASS_CEILING["deck_ui"]) == {"local_only", "cloud_redacted", "cloud_ok"}


def test_local_only_thread_cannot_link_to_cloud_channel() -> None:
    for channel in CLOUD_CHANNELS:
        with pytest.raises(ConversationError, match="local_only_cloud_link"):
            check_link_class(channel=channel, thread_class="local_only")
    check_link_class(channel="deck_ui", thread_class="local_only")
    check_link_class(channel="claude_code", thread_class="local_only")
    check_link_class(channel="grok", thread_class="cloud_ok")


def test_target_classes_and_codex_gets_nothing() -> None:
    assert target_classes("codex") == ()
    assert set(target_classes("ollama")) == {"local_only", "cloud_redacted", "cloud_ok"}
    for target in ("claude", "claude_code", "grok"):
        # cloud_redacted is excluded: redaction is task-bound and not applicable here.
        assert target_classes(target) == ("cloud_ok",)
        assert context_classes(target=target, data_class="cloud_redacted") == ("cloud_ok",)
        with pytest.raises(ConversationError, match="data_class_block"):
            context_classes(target=target, data_class="local_only")
    assert context_classes(target="ollama", data_class="cloud_ok") == ("cloud_ok",)
    with pytest.raises(ConversationError, match="unknown_target"):
        context_classes(target="chatgpt", data_class="cloud_ok")


class _ExplodingSession:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"codex context must not read the database ({name})")


@pytest.mark.parametrize("data_class", ["local_only", "cloud_redacted", "cloud_ok"])
def test_codex_context_is_empty_without_database_access(data_class: str) -> None:
    context = load_memory_context(
        _ExplodingSession(),  # type: ignore[arg-type]
        thread_id=None,
        target="codex",
        data_class=data_class,
        query="garden",
    )
    assert context.items == () and context.classes == () and context.tokens == 0


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)


@pytest.mark.parametrize(
    "detail",
    [
        {"text": "plain sentence"},
        {"reason": "two words"},
        {"reason": "x" * 200},
        {"message_id": 1.5},
    ],
)
def test_audit_detail_refuses_free_text(detail: dict[str, object]) -> None:
    session = _RecordingSession()
    with pytest.raises(ConversationError, match="invalid_audit_detail"):
        record_conversation_event(session, "assistant.turn_accepted", detail)  # type: ignore[arg-type]
    assert session.added == []


def test_audit_event_type_needs_conversation_prefix() -> None:
    session = _RecordingSession()
    with pytest.raises(ConversationError, match="invalid_audit_event"):
        record_conversation_event(session, "task.create", {})  # type: ignore[arg-type]


def _python_files() -> list[Path]:
    return sorted(Path("app").rglob("*.py"))


def test_only_the_approval_service_creates_approval_rows() -> None:
    creators = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "MemoryApproval"
            ):
                creators.append(path.as_posix())
            if isinstance(node, ast.Call) and "memory_approvals" in ast.unparse(node.func):
                creators.append(path.as_posix())
    assert set(creators) == {"app/memory/approval.py"}


def test_every_decision_function_requires_session_and_shown_hash() -> None:
    tree = ast.parse((MEMORY / "approval.py").read_text(encoding="utf-8"))
    deciders = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name.startswith(("decide_", "correct_")) or node.name == "create_owner_thread")
    ]
    assert {node.name for node in deciders} == {
        "decide_message",
        "decide_fact",
        "decide_thread_topic",
        "decide_thread_link",
        "create_owner_thread",
        "correct_message",
        "correct_fact",
        "correct_thread_topic",
        "correct_thread_link",
    }
    for node in deciders:
        names = {arg.arg for arg in node.args.kwonlyargs}
        assert {"session_token", "shown_sha256_hex"} <= names
        calls = {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
        assert "require_owner_session" in calls and "_check_shown" in calls


def test_memory_package_has_no_outbound_or_delete_code() -> None:
    forbidden_imports = {"socket", "http", "urllib", "httpx", "requests", "subprocess", "smtplib"}
    for path in sorted(MEMORY.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & forbidden_imports
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert not name.endswith(("delete", ".delete")), path
        assert "DELETE FROM" not in source.upper(), path


def test_new_approval_states_are_not_reachable_from_ingest_source() -> None:
    source = (MEMORY / "ingest.py").read_text(encoding="utf-8")
    assert '"approved"' not in source and "'approved'" not in source
    assert '"bewiesen"' not in source


def test_turn_fixture_is_not_mutated() -> None:
    payload = _turn()
    before = copy.deepcopy(payload)
    parse_turn(payload)
    assert payload == before


def test_account_shaped_secret_is_detected_without_a_stored_literal() -> None:
    account = "g" + "hp_" + "a" * 16
    assert contains_secret(account)
    rendered = render_fact_text(
        subject="box", predicate="holds", object_="key", sentence=f"keep {account}"
    )
    assert contains_secret(rendered)
    assert not contains_secret("box", "holds", "key", "keep the spare")


def test_approver_reference_is_not_the_session_token_hash() -> None:
    digest = "ab" * 32
    reference = approver_reference(digest)
    assert reference != digest
    assert len(reference) == 64
    assert reference == approver_reference(digest)


def test_shown_bytes_bind_decision_class_and_account() -> None:
    message = AssistantMessage(
        data_class="cloud_ok",
        source_channel="deck_ui",
        source_account="owner",
        role="owner",
        text="Hello",
    )
    approved = message_shown_bytes(message, decision="approved", mark_proven=True)
    rejected = message_shown_bytes(message, decision="rejected")
    assert approved != rejected
    assert approved.startswith(b"memory-shown.v1\n")
    assert b"kind=message\n" in approved
    assert b"data_class=cloud_ok\n" in approved
    assert b"account=owner\n" in approved
    assert b"role=owner\n" in approved
    assert b"privacy_hold=none\n" in approved
    assert b"decision=approved\n" in approved
    assert b"mark_proven=1\n" in approved
    assert approved.endswith(b"\n\nHello")
    topic = thread_shown_bytes(title="Garden", data_class="local_only", decision="approved")
    assert b"kind=thread\n" in topic and topic.endswith(b"\n\nGarden")
    assert shown_sha256(approved) != shown_sha256(rejected)


def test_shown_bytes_carry_the_hold_and_the_correction_marker() -> None:
    held = AssistantMessage(
        data_class="cloud_ok",
        source_channel="slack",
        source_account="owner",
        role="owner",
        text="Hello",
        privacy_hold="blocklist_unavailable",
    )
    first = message_shown_bytes(held, decision="approved")
    assert b"privacy_hold=blocklist_unavailable\n" in first
    assert b"correction=" not in first
    previous = uuid.uuid4()
    corrected = message_shown_bytes(held, decision="rejected", corrects=previous)
    assert b"correction=1\n" in corrected
    assert f"corrects_approval_id={previous}\n".encode() in corrected
    assert corrected.endswith(b"\n\nHello")
    plain = message_shown_bytes(held, decision="rejected")
    assert shown_sha256(corrected) != shown_sha256(plain)


def test_memory_privacy_hold_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DECK_BLOCKLIST_PATH", raising=False)
    clean = ("Plain note about the garden.",)
    assert memory_privacy_hold("local_only", clean, blocklist_required=True) is None
    assert memory_privacy_hold("cloud_ok", clean, blocklist_required=False) is None
    assert (
        memory_privacy_hold("cloud_ok", clean, blocklist_required=True) == "blocklist_unavailable"
    )
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(tmp_path / "missing-blocklist.txt"))
    assert (
        memory_privacy_hold("cloud_ok", clean, blocklist_required=False) == "blocklist_unavailable"
    )
    path = tmp_path / "blocklist.txt"
    path.write_text("gardenword\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    assert memory_privacy_hold("cloud_ok", clean, blocklist_required=True) is None
    hit = ("A gardenword note.",)
    assert memory_privacy_hold("cloud_ok", hit, blocklist_required=True) == "privacy_block"
    # A blocklist hit alone is redactable and does not hold cloud_redacted.
    assert memory_privacy_hold("cloud_redacted", hit, blocklist_required=True) is None
    assert memory_privacy_hold("local_only", hit, blocklist_required=True) is None


def test_strongest_hold_prefers_privacy_block() -> None:
    assert strongest_hold(None, None) is None
    assert strongest_hold("blocklist_unavailable", None) == "blocklist_unavailable"
    assert strongest_hold("blocklist_unavailable", "privacy_block") == "privacy_block"
    assert strongest_hold(None, "privacy_block") == "privacy_block"


def test_hold_codes_match_migration_check() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20261110_0064_channels_and_memory.py"
    ).read_text(encoding="utf-8")
    listed = ", ".join(f"'{code}'" for code in MEMORY_HOLD_CODES)
    assert f"privacy_hold IN ({listed})" in migration
