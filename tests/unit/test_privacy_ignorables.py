"""Default-ignorable Unicode cannot split scanner or redaction findings."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.privacy.config import get_privacy_settings
from app.privacy.normalization import normalize_shadow, normalize_text
from app.privacy.scanner import FindingKind, scan_text
from app.redaction.engine import redact, rehydrate


@pytest.mark.parametrize(
    "invisible",
    [
        "\u034f",  # Combining grapheme joiner (Mn).
        "\u115f",
        "\u1160",
        "\u3164",
        "\uffa0",  # Hangul fillers (Lo), including compatibility forms.
        "\u17b4",
        "\u17b5",  # Khmer inherent vowels (Mn).
        "\u180b",
        "\u180c",
        "\u180d",
        "\u180f",  # Mongolian variation selectors (Mn).
        "\ufe00",
        "\ufe0f",
        "\U000e0100",
        "\U000e01ef",  # Both ends of both variation-selector blocks.
        "\u2065",
        "\ufff0",
        "\ufff8",
        "\U000e0000",
        "\U000e0080",
        "\U000e01f0",
        "\U000e0fff",  # Reserved default-ignorable code points (Cn).
        "\u200b",
        "\U0001bca0",
        "\U0001d17a",  # Format-category members remain covered.
    ],
)
def test_default_ignorables_cannot_separate_synthetic_term(invisible: str) -> None:
    original = "SYN" + invisible + "THETIC"
    shadow = normalize_shadow(original)
    assert shadow.text == "SYNTHETIC"
    assert normalize_text(original) == shadow.text
    assert shadow.original_span(0, len(shadow.text)) == (0, len(original))
    findings = scan_text(original, source="title", blocklist=("SYNTHETIC",))
    assert len(findings) == 1
    assert findings[0].rule == "blocklist"
    assert (findings[0].start, findings[0].end) == (0, len(original))


def test_cgj_obfuscated_synthetic_aws_key_is_fully_detected() -> None:
    marker = "AKI\u034fA" + "A" * 16
    original = "prefix " + marker + " suffix"
    findings = scan_text(original, source="instructions")
    assert len(findings) == 1
    assert findings[0].kind == FindingKind.SECRET
    assert findings[0].rule == "aws_access_key"
    assert original[findings[0].start : findings[0].end] == marker


@pytest.mark.parametrize("selector", ["\ufe0f", "\U000e0100", "\U000e01ef"])
def test_variation_selector_blocklist_match_preserves_original(selector: str) -> None:
    original = "SYN" + selector + "THETIC TERM"
    task_id = uuid4()
    result = redact(original, task_id=task_id, blocklist=("SYNTHETIC TERM",))
    assert result.kinds == {"TERM": 1}
    assert list(result.mapping.values()) == [original]
    assert result.text == next(iter(result.mapping))
    assert rehydrate(result.text, task_id=task_id, mapping=result.mapping) == original


def test_email_is_fully_replaced_with_mixed_ignorables_and_offsets() -> None:
    email = "syn\u034fthe\ufe0ftic" + "@" + "exam\U000e0100ple.invalid"
    original = "prefix " + email + " suffix"
    task_id = uuid4()
    result = redact(original, task_id=task_id)
    assert result.kinds == {"EMAIL": 1}
    assert list(result.mapping.values()) == [email]
    token = next(iter(result.mapping))
    assert result.text == "prefix " + token + " suffix"
    assert "@" not in result.text
    assert "synthetic" not in normalize_text(result.text)
    assert rehydrate(result.text, task_id=task_id, mapping=result.mapping) == original


def test_ignorables_do_not_prevent_canonical_composition() -> None:
    original = "A\u034f\u030a"
    shadow = normalize_shadow(original)
    assert shadow.text == "Å"
    assert shadow.original_span(0, 1) == (0, 3)
    assert normalize_text(original) == "Å"


def test_blocklist_loader_and_scanner_strip_identical_ignorables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "literals.txt"
    path.write_text("SYN\ufe0fTHETIC\nSYN\U000e0100THETIC\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    settings = get_privacy_settings()
    assert settings.blocklist == ("SYNTHETIC",)
    matches = scan_text("SYN\u034fTHETIC", source="title", blocklist=settings.blocklist)
    assert len(matches) == 1
    assert matches[0].rule == "blocklist"


@pytest.mark.parametrize("visible", ["\u034e", "\u1161", "\u3163", "\uffef", "\ufff9"])
def test_removal_does_not_expand_into_neighboring_visible_characters(visible: str) -> None:
    import unicodedata

    expected = (
        "" if unicodedata.category(visible) == "Cf" else unicodedata.normalize("NFKC", visible)
    )
    assert normalize_text(visible) == expected
