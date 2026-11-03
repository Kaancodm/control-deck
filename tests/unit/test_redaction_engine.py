"""Synthetic task-scoped redaction and lossless local rehydration contracts."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.privacy.scanner import Finding, FindingKind
from app.redaction import engine
from app.redaction.engine import RedactionBlockedError, RedactionError, redact, rehydrate
from app.redaction.placeholders import (
    MAX_PLACEHOLDER_INDEX,
    RedactionKind,
    format_placeholder,
    parse_placeholder,
)


@pytest.fixture
def task_id() -> UUID:
    return uuid4()


def _email() -> str:
    return "synthetic" + "@" + "example.invalid"


@pytest.mark.parametrize(
    "value,kind,blocklist",
    [
        (_email(), RedactionKind.EMAIL, ()),
        ("+41 " + "99 000 00 00", RedactionKind.PHONE, ()),
        ("CH00" + "0" * 17, RedactionKind.IBAN, ()),
        ("756." + "0000.0000.00", RedactionKind.AHV, ()),
        (".".join(map(str, (10, 1, 2, 3))), RedactionKind.IP, ()),
        ("fd00" + "::1", RedactionKind.IP, ()),
        ("SYNTHETIC PROJECT", RedactionKind.TERM, ("SYNTHETIC PROJECT",)),
    ],
)
def test_supported_kinds_roundtrip_without_value_disclosure(
    task_id: UUID, value: str, kind: RedactionKind, blocklist: tuple[str, ...]
) -> None:
    source = "Prefix 🧪 " + value + " suffix"
    outcome = redact(source, task_id=task_id, blocklist=blocklist)
    token = format_placeholder(task_id, kind, 1)
    assert outcome.text == "Prefix 🧪 " + token + " suffix"
    assert outcome.mapping == {token: value}
    assert outcome.kinds == {kind.value: 1}
    assert outcome.applied and outcome.placeholders == 1
    assert value not in outcome.text
    assert value not in repr(outcome)
    assert source not in repr(outcome)
    assert rehydrate(outcome.text, task_id=task_id, mapping=outcome.mapping) == source


def test_repeated_values_reuse_one_placeholder(task_id: UUID) -> None:
    text = " ; ".join([_email()] * 3)
    outcome = redact(text, task_id=task_id)
    assert outcome.placeholders == 1
    assert len(outcome.mapping) == 1
    assert outcome.text.count(next(iter(outcome.mapping))) == 3
    assert rehydrate(outcome.text, task_id=task_id, mapping=outcome.mapping) == text


def test_existing_map_is_stable_and_not_mutated(task_id: UUID) -> None:
    first = redact(_email(), task_id=task_id)
    before = dict(first.mapping)
    other = "other" + "@" + "example.invalid"
    second = redact(other + " ; " + _email(), task_id=task_id, existing=first.mapping)
    assert first.mapping == before
    assert second.mapping[format_placeholder(task_id, RedactionKind.EMAIL, 1)] == _email()
    assert second.mapping[format_placeholder(task_id, RedactionKind.EMAIL, 2)] == other
    assert second.placeholders == 2


def test_changed_blocklist_does_not_renumber_the_same_original(task_id: UUID) -> None:
    first = redact(_email(), task_id=task_id)
    second = redact(_email(), task_id=task_id, existing=first.mapping, blocklist=(_email(),))
    assert second.mapping == first.mapping
    assert second.text == first.text
    assert second.kinds == {"EMAIL": 1}


def test_known_tokens_are_idempotent(task_id: UUID) -> None:
    first = redact(_email(), task_id=task_id)
    second = redact(first.text, task_id=task_id, existing=first.mapping)
    assert second == first


def test_unused_existing_mapping_does_not_mark_plain_text_redacted(task_id: UUID) -> None:
    first = redact(_email(), task_id=task_id)
    second = redact("ordinary text", task_id=task_id, existing=first.mapping)
    assert second.mapping == first.mapping
    assert second.text == "ordinary text"
    assert second.placeholders == 0 and not second.applied


@pytest.mark.parametrize(
    "source,blocklist",
    [
        ("🧪 synthetic\u200b" + "@" + "example.invalid", ()),
        ("🧪 ｓｙｎｔｈｅｔｉｃ" + "＠" + "ｅｘａｍｐｌｅ．ｉｎｖａｌｉｄ", ()),
        ("🧪 SYNTHETIC CAFE\u0301", ("SYNTHETIC CAFÉ",)),
        ("🧪 SYNTHETIC OﬃCE", ("SYNTHETIC OFFICE",)),
        ("🧪 SYNTHETIC\u00a0PROJECT", ("SYNTHETIC PROJECT",)),
        ("🧪 SYNTHETIC\u200d PROJECT", ("SYNTHETIC PROJECT",)),
    ],
)
def test_normalized_detection_preserves_original_spelling(
    task_id: UUID, source: str, blocklist: tuple[str, ...]
) -> None:
    result = redact(source, task_id=task_id, blocklist=blocklist)
    assert result.applied
    assert source not in result.text
    assert rehydrate(result.text, task_id=task_id, mapping=result.mapping) == source


def test_distinct_unicode_spellings_have_distinct_lossless_values(task_id: UUID) -> None:
    source = "SYNTHETIC CAFÉ ; SYNTHETIC CAFE\u0301"
    result = redact(source, task_id=task_id, blocklist=("SYNTHETIC CAFÉ",))
    assert len(result.mapping) == 2
    assert rehydrate(result.text, task_id=task_id, mapping=result.mapping) == source


@pytest.mark.parametrize("operation", ["redact", "rehydrate"])
def test_unknown_placeholder_cannot_be_resolved(task_id: UUID, operation: str) -> None:
    token = format_placeholder(task_id, RedactionKind.EMAIL, 1)
    with pytest.raises(RedactionError) as failure:
        if operation == "redact":
            redact(token, task_id=task_id)
        else:
            rehydrate(token, task_id=task_id, mapping={})
    assert failure.value.code == "unknown_placeholder"
    assert token not in str(failure.value)


def test_foreign_token_does_not_resolve_by_matching_kind_and_number(task_id: UUID) -> None:
    local = redact(_email(), task_id=task_id)
    foreign = redact("foreign" + "@" + "example.invalid", task_id=uuid4())
    with pytest.raises(RedactionError, match="unknown_placeholder"):
        rehydrate(foreign.text, task_id=task_id, mapping=local.mapping)
    with pytest.raises(RedactionError, match="invalid_mapping"):
        rehydrate(foreign.text, task_id=task_id, mapping=foreign.mapping)


@pytest.mark.parametrize(
    "transform",
    [
        lambda value: value[:-1],
        lambda value: value.replace("EMAIL", "OTHER"),
        lambda value: value.lower(),
        lambda value: value.replace("CD_", "C\u200bD_"),
        lambda value: value.replace("[[", "［［"),
        lambda value: value.replace("_1]]", "_01]]"),
    ],
)
def test_malformed_or_obfuscated_tokens_fail_closed(task_id: UUID, transform) -> None:
    original = redact(_email(), task_id=task_id)
    malformed = transform(original.text)
    for operation in (
        lambda: redact(malformed, task_id=task_id, existing=original.mapping),
        lambda: rehydrate(malformed, task_id=task_id, mapping=original.mapping),
    ):
        with pytest.raises(RedactionError) as failure:
            operation()
        assert malformed not in str(failure.value)
        assert _email() not in str(failure.value)


def test_duplicate_original_mapping_is_rejected(task_id: UUID) -> None:
    existing = {
        format_placeholder(task_id, RedactionKind.EMAIL, index): _email() for index in (1, 2)
    }
    with pytest.raises(RedactionError, match="duplicate_mapping"):
        redact("ordinary text", task_id=task_id, existing=existing)


def test_duplicate_original_across_kinds_is_rejected(task_id: UUID) -> None:
    existing = {
        format_placeholder(task_id, kind, 1): _email()
        for kind in (RedactionKind.EMAIL, RedactionKind.TERM)
    }
    with pytest.raises(RedactionError, match="duplicate_mapping"):
        redact("ordinary text", task_id=task_id, existing=existing)


def test_mapping_cannot_insert_a_token_for_recursive_rehydration(task_id: UUID) -> None:
    first = format_placeholder(task_id, RedactionKind.TERM, 1)
    second = format_placeholder(task_id, RedactionKind.TERM, 2)
    with pytest.raises(RedactionError, match="invalid_mapping"):
        rehydrate(first, task_id=task_id, mapping={first: second, second: "synthetic phrase"})


@pytest.mark.parametrize(
    "value",
    [
        "sk-TEST-" + "SYNTHETIC-NOT-A-CREDENTIAL",
        "DECK_OWNER_TOKEN=" + "synthetic-value",
        "/home/" + "synthetic/file.txt",
        "synthetic" + ".internal",
    ],
)
def test_secrets_and_unsupported_identifiers_never_enter_mapping(task_id: UUID, value: str) -> None:
    existing: dict[str, str] = {}
    with pytest.raises(RedactionBlockedError) as failure:
        redact(value, task_id=task_id, existing=existing)
    assert existing == {}
    assert value not in str(failure.value)
    assert value not in repr(failure.value)


def test_residual_scan_blocks_finding_cap_overflow(task_id: UUID) -> None:
    source = " ; ".join([_email()] * 129)
    with pytest.raises(RedactionBlockedError, match="residual_findings"):
        redact(source, task_id=task_id)


def test_residual_scan_checks_generated_tokens_against_blocklist(task_id: UUID) -> None:
    with pytest.raises(RedactionBlockedError, match="residual_findings"):
        redact(_email(), task_id=task_id, blocklist=("[[",))


def test_scanner_overlap_uses_the_protected_literal_without_corruption(task_id: UUID) -> None:
    source = "Prefix " + _email() + " suffix"
    result = redact(source, task_id=task_id, blocklist=(_email(),))
    assert result.kinds == {"TERM": 1}
    assert rehydrate(result.text, task_id=task_id, mapping=result.mapping) == source


def test_invalid_adapter_spans_fail_closed(task_id: UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine,
        "scan_text",
        lambda *args, **kwargs: [Finding(FindingKind.REDACTABLE_PII, "email", "payload", -1, 3)],
    )
    with pytest.raises(RedactionBlockedError, match="invalid_finding_span"):
        redact("ordinary text", task_id=task_id)


def test_normalization_failure_uses_safe_redaction_error(
    task_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.privacy.normalization import NormalizationError

    def fail_scan(*args, **kwargs):
        raise NormalizationError()

    monkeypatch.setattr(engine, "scan_text", fail_scan)
    with pytest.raises(RedactionBlockedError, match="normalization_failed"):
        redact("ordinary text", task_id=task_id)


def test_placeholder_counter_cannot_overflow(task_id: UUID) -> None:
    existing = {format_placeholder(task_id, RedactionKind.EMAIL, MAX_PLACEHOLDER_INDEX): _email()}
    with pytest.raises(RedactionBlockedError, match="placeholder_limit"):
        redact("other" + "@" + "example.invalid", task_id=task_id, existing=existing)


def test_placeholder_parser_validates_task_scope_and_kind(task_id: UUID) -> None:
    token = format_placeholder(task_id, RedactionKind.EMAIL, 1)
    parsed = parse_placeholder(token)
    assert (parsed.task_id, parsed.kind, parsed.index) == (task_id, RedactionKind.EMAIL, 1)
    with pytest.raises(ValueError):
        parse_placeholder(token + "suffix")
    with pytest.raises(ValueError):
        format_placeholder(task_id, RedactionKind.EMAIL, 0)
