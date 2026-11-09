"""Dialogue prompt budgeting and history normalization."""

from __future__ import annotations

import pytest

from app.assistant.dialogue import compose_dialogue_messages, estimate_dialogue_tokens
from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import LOOPBACK_NOTICE, normalize_history_reply


def test_normalize_history_strips_legacy_notice_and_thinking() -> None:
    raw = f"{LOOPBACK_NOTICE} <think>x</think> Antwort"
    assert normalize_history_reply(raw) == "Antwort"


def test_compose_messages_orders_roles_and_keeps_current_user() -> None:
    messages = compose_dialogue_messages(
        message="Heute",
        profile_notes="Profilnotiz",
        history=(("Alt", f"{LOOPBACK_NOTICE} früher"),),
        num_ctx=4096,
        num_predict=400,
    )
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "Heute"}
    assert any(m["role"] == "assistant" and m["content"] == "früher" for m in messages)


def test_budget_drops_oldest_turn_first() -> None:
    heavy = "x" * 900
    messages = compose_dialogue_messages(
        message="Neu",
        profile_notes=None,
        history=(("erste", "a" * 200), ("zweite", "b" * 200), ("dritte", heavy)),
        num_ctx=512,
        num_predict=64,
    )
    joined = " ".join(m["content"] for m in messages)
    assert "erste" not in joined
    assert messages[-1]["content"] == "Neu"


def test_oversized_budget_configuration_is_rejected() -> None:
    with pytest.raises(AssistantError) as caught:
        compose_dialogue_messages(
            message="x",
            profile_notes=None,
            history=(),
            num_ctx=200,
            num_predict=200,
        )
    assert caught.value.code == "assistant_model_unconfigured"


def test_estimate_tokens_uses_len_over_three_point_five() -> None:
    text = "a" * 35
    assert estimate_dialogue_tokens(text) == 10


def test_current_user_message_keeps_line_breaks() -> None:
    messages = compose_dialogue_messages(
        message="Zeile1\nZeile2",
        profile_notes=None,
        history=(),
        num_ctx=4096,
        num_predict=400,
    )
    assert messages[-1]["content"] == "Zeile1\nZeile2"
