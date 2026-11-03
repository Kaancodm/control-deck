"""Multiword blocklist separators preserve literal matches and original offsets."""

from uuid import UUID

import pytest

from app.privacy.scanner import FindingKind, scan_text
from app.redaction.engine import redact, rehydrate

_TASK_ID = UUID("00000000-0000-4000-8000-000000000007")


@pytest.mark.parametrize("separator", [" ", "   ", "\t", "\n", "\r\n", "\n \t", "\u00a0", "\u202f"])
def test_multiword_term_accepts_any_whitespace_run(separator: str) -> None:
    term = "SYNTHETIC" + separator + "PROJECT"
    text = "ordinary prefix " + term + " suffix"
    findings = scan_text(text, source="instructions", blocklist=("synthetic project",))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == "blocklist"
    assert finding.kind == FindingKind.PROTECTED_IDENTIFIER
    assert finding.start == len("ordinary prefix ")
    assert text[finding.start : finding.end] == term


@pytest.mark.parametrize(
    "entry", ["SYNTHETIC  PROJECT", "SYNTHETIC\tPROJECT", "SYNTHETIC\u00a0PROJECT"]
)
def test_configured_whitespace_runs_have_the_same_matching_semantics(entry: str) -> None:
    text = "SYNTHETIC\n\tPROJECT"
    findings = scan_text(text, source="instructions", blocklist=(entry,))
    assert [(item.start, item.end) for item in findings] == [(0, len(text))]


@pytest.mark.parametrize(
    "text", ["SYNTHETICPROJECT", "SYNTHETIC-PROJECT", "XSYNTHETIC PROJECT", "SYNTHETIC PROJECTX"]
)
def test_multiword_term_requires_separators_and_retains_word_boundaries(text: str) -> None:
    assert scan_text(text, source="instructions", blocklist=("SYNTHETIC PROJECT",)) == []


def test_regex_metacharacters_and_backslashes_remain_literal() -> None:
    term = "X.*[Y]\\Z\t(Q)+"
    text = "XabcY\\Z (QQQ) then " + term
    findings = scan_text(text, source="instructions", blocklist=(r"X.*[Y]\Z (Q)+",))
    assert [text[item.start : item.end] for item in findings] == [term]


def test_shared_prefixes_still_choose_the_longest_complete_phrase() -> None:
    text = "SYNTHETIC\nPROJECT\tALPHA and SYNTHETIC\tPROJECT"
    findings = scan_text(
        text,
        source="instructions",
        blocklist=("SYNTHETIC", "SYNTHETIC PROJECT", "SYNTHETIC PROJECT ALPHA"),
    )
    assert [text[item.start : item.end] for item in findings] == [
        "SYNTHETIC\nPROJECT\tALPHA",
        "SYNTHETIC\tPROJECT",
    ]


def test_normalized_phrase_offsets_and_redaction_roundtrip_include_all_whitespace() -> None:
    term = "ＣＡＦＥ\u0301\n\tＰＲＯＪＥＣＴ"
    text = "🧪 prefix " + term + " suffix"
    blocklist = ("café project",)
    findings = scan_text(text, source="instructions", blocklist=blocklist)
    assert len(findings) == 1
    assert text[findings[0].start : findings[0].end] == term

    outcome = redact(text, task_id=_TASK_ID, blocklist=blocklist)
    assert outcome.placeholders == 1
    assert list(outcome.mapping.values()) == [term]
    token = next(iter(outcome.mapping))
    assert outcome.text == "🧪 prefix " + token + " suffix"
    assert scan_text(outcome.text, source="payload", blocklist=blocklist) == []
    assert rehydrate(outcome.text, task_id=_TASK_ID, mapping=outcome.mapping) == text
