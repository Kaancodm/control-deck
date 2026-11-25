"""Conversation audit rows keep ids and flags and drop message text."""

from datetime import UTC, datetime
from uuid import uuid4

from app.audit.presentation import project_record

_MESSAGE = uuid4()


def test_conversation_projection_keeps_refs_and_drops_text() -> None:
    sentence = "private-conversation-sentence"
    item = project_record(
        {
            "source": "conversation",
            "id": 9,
            "created_at": datetime(2026, 11, 24, tzinfo=UTC),
            "event_type": "memory.fact_corrected",
            "task_id": None,
            "operation_id": None,
            "actor": "operator",
            "attempt": None,
            "from_status": None,
            "to_status": None,
            "phase": None,
            "detail": {
                "reason": "unknown_account",
                "decision": "approved",
                "evidence_status": "unbewiesen",
                "privacy_hold": "privacy_block",
                "message_id": str(_MESSAGE),
                "text_sha256": "ab" * 32,
                "cleared_valid_to": True,
                "cleared_superseded_by": False,
                "text": sentence,
            },
        }
    )
    dumped = item.model_dump_json()
    assert sentence not in dumped
    assert item.event_type == "memory.fact_corrected"
    assert item.reason == "unknown_account"
    assert item.privacy_hold == "privacy_block"
    assert item.decision == "approved"
    assert item.refs == {"message_id": str(_MESSAGE)}
    assert item.digests == {"text_sha256": "ab" * 32}
    assert item.cleared_valid_to is True
    assert item.cleared_superseded_by is False


def test_task_projection_ignores_conversation_flags() -> None:
    item = project_record(
        {
            "source": "task",
            "id": 4,
            "created_at": datetime(2026, 11, 24, tzinfo=UTC),
            "event_type": "task.approve",
            "task_id": uuid4(),
            "operation_id": None,
            "actor": "operator",
            "attempt": 1,
            "from_status": None,
            "to_status": None,
            "phase": None,
            "detail": {
                "cleared_valid_to": True,
                "privacy_hold": "privacy_block",
                "message_id": str(_MESSAGE),
            },
        }
    )
    assert item.cleared_valid_to is False
    assert item.privacy_hold is None
    assert item.refs == {}
