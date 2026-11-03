"""Unicode shadow scanning keeps original spans and bounded normalization work."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from random import Random

import pytest

from app.privacy.config import PrivacyConfigurationError, get_privacy_settings
from app.privacy.normalization import NormalizationError, normalize_shadow, normalize_text
from app.privacy.scanner import FindingKind, scan_text


def _expected(text: str) -> str:
    # The non-Cf members of the Unicode 15.0 default-ignorable property.
    invisible = re.compile(
        r"[\u034f\u115f-\u1160\u17b4-\u17b5\u180b-\u180d\u180f"
        r"\u2065\u3164\ufe00-\ufe0f\uffa0\ufff0-\ufff8\U000e0000-\U000e0fff]"
    )
    stripped = invisible.sub("", text)
    return unicodedata.normalize(
        "NFKC", "".join(char for char in stripped if unicodedata.category(char) != "Cf")
    )


@pytest.mark.parametrize(
    "text",
    [
        "ordinary ASCII text",
        "",
        "\u200b\u200d\u00ad\ufeff",
        "prefix e\u0301 suffix",
        "A\u0315\u0300",
        "\u0301\u0323A",
        "\u1100\u1161\u11a8",
        "\u3131\u314f",
        "\u09c7\u09be",
        "\u0cc6\u0cc2\u0cd5",
        "\ufb03\u0301",
        "A\u200d\u030a",
        "fullwidth: ｓｙｎｔｈｅｔｉｃ",
        "space\u00a0and\u202fspace",
        "\u212b\u0323 and \u00c5\u0323",
    ],
)
def test_shadow_matches_nfkc_of_format_stripped_original(text: str) -> None:
    expected = _expected(text)
    assert normalize_shadow(text).text == expected
    assert normalize_text(text) == expected


def test_generated_sequences_match_python_normalization() -> None:
    random = Random(604)
    alphabet = list("Synthetic ") + [
        "\u0300",
        "\u0301",
        "\u0315",
        "\u0323",
        "\u0345",
        "\u00ad",
        "\u200d",
        "\ufb03",
        "\ufdfa",
        "\u1100",
        "\u1161",
        "\u11a8",
        "\u3131",
        "\u314f",
        "\u09c7",
        "\u09be",
        "\u0cc6",
        "\u0cc2",
        "\u0cd5",
        "\u212b",
        "\uff41",
    ]
    alphabet.extend(chr(random.randrange(0x110000)) for _ in range(500))
    for _ in range(1000):
        text = "".join(random.choices(alphabet, k=random.randrange(1, 60)))
        shadow = normalize_shadow(text)
        assert shadow.text == _expected(text)
        assert normalize_text(text) == shadow.text
        for index in range(len(shadow.text)):
            start, end = shadow.original_span(index, index + 1)
            assert 0 <= start < end <= len(text)


def test_ascii_identity_does_not_allocate_position_maps() -> None:
    text = "ordinary text" * 1000
    shadow = normalize_shadow(text)
    assert shadow.text is text
    assert shadow._starts is None and shadow._ends is None
    assert shadow.original_span(4, 8) == (4, 8)


@pytest.mark.parametrize(
    "text,span,expected",
    [
        ("e\u0301", (0, 1), (0, 2)),
        ("\ufb03", (0, 1), (0, 1)),
        ("\ufb03", (1, 3), (0, 1)),
        ("A\u0315\u0300", (0, 1), (0, 3)),
        ("A\u0315\u0300", (1, 2), (1, 2)),
        ("A\u200d\u030a", (0, 1), (0, 3)),
        ("\u1100\u1161\u11a8", (0, 1), (0, 3)),
        ("\u09c7\u09be", (0, 1), (0, 2)),
        ("a\u200bb", (0, 2), (0, 3)),
        ("\u200babc\u200d", (0, 3), (1, 4)),
    ],
)
def test_spans_cover_original_contributors(
    text: str, span: tuple[int, int], expected: tuple[int, int]
) -> None:
    shadow = normalize_shadow(text)
    assert shadow.original_span(*span) == expected
    assert text not in repr(shadow)


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (2, 1), (0, 10)])
def test_invalid_span_does_not_echo_original(start: int, end: int) -> None:
    shadow = normalize_shadow("private")
    with pytest.raises(ValueError) as rejected:
        shadow.original_span(start, end)
    assert "private" not in str(rejected.value)


@pytest.mark.parametrize(
    "text,rule",
    [
        ("AKI\u00adA" + "A" * 16, "aws_access_key"),
        ("Bearer\u200d " + "x" * 20, "bearer_token"),
        ("-----BEGIN\u00a0PRIVATE\u202fKEY-----", "private_key"),
        ("ｓｋ-TEST-SYNTHETIC-MARKER", "provider_api_key"),
        ("synthe\u200btic" + "@" + "example.invalid", "email"),
        ("CH00" + "0" * 8 + "\u200b" + "0" * 9, "iban"),
        ("756." + "0000.\u200b0000.00", "ahv"),
        ("DECK_OWNER_TO\u200bKEN=synthetic", "credential_assignment"),
    ],
)
def test_obfuscated_catalog_matches_original_offsets(text: str, rule: str) -> None:
    wrapped = "🧪 prefix " + text + " suffix"
    findings = scan_text(wrapped, source="instructions")
    matches = [finding for finding in findings if finding.rule == rule]
    assert len(matches) == 1
    finding = matches[0]
    assert wrapped[finding.start : finding.end] == text
    assert text not in repr(findings)


@pytest.mark.parametrize(
    "text,entry",
    [
        ("Cafe\u0301-SYNTH", "Café-SYNTH"),
        ("Café-SYNTH", "Cafe\u0301-SYNTH"),
        ("SYN\u200bTHETIC", "SYNTHETIC"),
        ("SYNTHETIC", "SYN\u200dTHETIC"),
        ("ＳＹＮＴＨＥＴＩＣ", "SYNTHETIC"),
        ("SYNTHETIC", "ＳＹＮＴＨＥＴＩＣ"),
        ("SYNTHETIC\u00a0PROJECT", "SYNTHETIC PROJECT"),
        ("\ufb03 SYNTHETIC", "ffi SYNTHETIC"),
    ],
)
def test_blocklist_and_text_share_normalization(text: str, entry: str) -> None:
    findings = scan_text(text, source="title", blocklist=(entry,))
    assert len(findings) == 1
    assert findings[0].rule == "blocklist"
    assert text[findings[0].start : findings[0].end] == text


def test_expansion_matches_are_deduplicated_in_original_space() -> None:
    findings = scan_text("\u2100", source="title", blocklist=("a/", "/c"))
    assert len(findings) == 1
    assert (findings[0].start, findings[0].end) == (0, 1)


def test_normalization_does_not_claim_homoglyph_detection() -> None:
    assert scan_text("SYNTHЕTIC", source="title", blocklist=("SYNTHETIC",)) == []


def test_normalized_secret_retains_priority_at_finding_limit() -> None:
    email = "synthetic" + "@" + "example.invalid "
    marker = "ｓｋ-TEST-SYNTHETIC-MARKER"
    text = email * 200 + marker
    findings = scan_text(text, source="instructions")
    assert len(findings) == 128
    assert any(
        item.kind == FindingKind.SECRET and text[item.start : item.end] == marker
        for item in findings
    )


def test_blocklist_loader_normalizes_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "literals.txt"
    path.write_text("Cafe\u0301-SYNTH\nCafé-SYNTH\nSYN\u200bTHETIC\u00a0PROJECT\n")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    settings = get_privacy_settings()
    assert settings.blocklist == ("Café-SYNTH", "SYNTHETIC PROJECT")


@pytest.mark.parametrize("literal", ["\u200b", "\ufdfa" * 30])
def test_blocklist_loader_rejects_empty_or_expanded_oversized_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, literal: str
) -> None:
    path = tmp_path / "literals.txt"
    path.write_text(literal)
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    with pytest.raises(PrivacyConfigurationError) as rejected:
        get_privacy_settings()
    assert literal not in str(rejected.value)


@pytest.mark.parametrize("text", ["A" + "\u0301" * 1024, "\u0301" * 1025])
def test_pathological_combining_runs_fail_closed(text: str) -> None:
    with pytest.raises(NormalizationError) as rejected:
        normalize_shadow(text)
    assert text not in str(rejected.value)


def test_expansion_limit_rejects_without_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.privacy import normalization

    monkeypatch.setattr(normalization, "_MAX_SHADOW_CHARACTERS", 12)
    with pytest.raises(NormalizationError):
        normalize_shadow("\ufdfa")
