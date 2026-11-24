"""Prompt labels and the deck thread id. The loader's class filter stays untouched."""

from __future__ import annotations

import pytest

from app.assistant.dialogue import compose_dialogue_messages
from app.assistant.errors import AssistantError
from app.assistant.memory_dialogue import (
    DIALOGUE_TARGET,
    normalize_external_thread_id,
    render_memory_block,
)
from app.memory.context import MemoryContext, MemoryItem
from app.memory.policy import CLOUD_TARGETS


def test_dialogue_target_is_local_and_neither_cloud_nor_codex() -> None:
    assert DIALOGUE_TARGET == "ollama"
    assert DIALOGUE_TARGET not in CLOUD_TARGETS
    assert DIALOGUE_TARGET != "codex"


def test_empty_thread_id_uses_the_deck_default() -> None:
    assert normalize_external_thread_id("") == "deck"
    assert normalize_external_thread_id("  deck-topic  ") == "deck-topic"


@pytest.mark.parametrize("value", ["http://topic", "has space", "x" * 201, "://nope"])
def test_thread_id_rejects_values_the_envelope_would_reject(value: str) -> None:
    with pytest.raises(AssistantError) as refused:
        normalize_external_thread_id(value)
    assert refused.value.code == "assistant_input"


def test_unproven_rows_are_labeled_and_profile_is_not_repeated() -> None:
    context = MemoryContext(
        target="ollama",
        classes=("local_only",),
        items=(
            MemoryItem("profile", "profile:1", "local_only", None, "owner-typed", "Profilsatz"),
            MemoryItem("history", "h1", "local_only", "owner", "unbewiesen", "THREADTOKEN"),
            MemoryItem("history", "h2", "local_only", "assistant", "bewiesen", "REPLYTOKEN"),
            MemoryItem("fact", "f1", "local_only", None, "unbewiesen", "orchard | needs | water"),
        ),
    )
    block = render_memory_block(context)
    assert block is not None
    assert "Profilsatz" not in block
    assert "owner: [unbewiesen] THREADTOKEN" in block
    assert "assistant: REPLYTOKEN" in block
    assert "[unbewiesen] REPLYTOKEN" not in block
    assert "fakt: [unbewiesen] orchard | needs | water" in block
    messages = compose_dialogue_messages(
        message="Heute",
        profile_notes=None,
        history=(),
        num_ctx=4096,
        num_predict=400,
        memory_context=block,
    )
    assert messages[1]["role"] == "system"
    assert "THREADTOKEN" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "Heute"}


def test_empty_memory_adds_no_block() -> None:
    assert render_memory_block(MemoryContext(target="ollama", classes=("local_only",))) is None
