"""Fixed labels for conversation reasons. Free text never becomes a label."""

from app.memory.reasons import CONVERSATION_REASONS, UNKNOWN_SOURCE_CODES
from app.ui.memory_labels import REASON_LABELS, hold_text, reason_text


def test_unknown_source_codes_name_the_plan_code() -> None:
    for code in UNKNOWN_SOURCE_CODES:
        assert reason_text(code) == f"{REASON_LABELS[code]} (Plan: unknown_source)"


def test_every_conversation_reason_has_a_fixed_label() -> None:
    assert set(REASON_LABELS) == set(CONVERSATION_REASONS)
    for code in CONVERSATION_REASONS:
        assert reason_text(code)


def test_free_text_and_unknown_holds_stay_blank() -> None:
    assert reason_text("Bitte freigeben") == ""
    assert reason_text("Unknown Reason") == ""
    assert hold_text("privacy_block") == "Datenschutz-Treffer"
    assert hold_text("secret_rejected") == ""
    assert hold_text(None) == ""
