"""Core acceptance checks are authoritative and fail closed on invalid inputs."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.core.acceptance import evaluate_acceptance
from app.core.schemas import AcceptanceRule, AcceptanceType


def _rule(kind: str, value: str | None = None) -> AcceptanceRule:
    return AcceptanceRule(type=AcceptanceType(kind), value=value)


@pytest.mark.parametrize(
    ("kind", "value", "text", "expected"),
    [
        ("must_contain", "answer", "The answer is ready.", True),
        ("must_contain", "answer", "No match.", False),
        ("must_not_contain", "secret", "Public output.", True),
        ("must_not_contain", "secret", "Contains a secret.", False),
        ("regex", r"^category:\s+(\w+)\s+\1$", "category: ready ready", True),
        ("regex", r"^category:", "missing category", False),
        ("regex", "[", "output", False),
        ("max_chars", "3", "abc", True),
        ("max_chars", "3", "abcd", False),
        ("max_chars", "0", "", True),
        ("max_chars", "1", "ä", True),
        ("max_chars", "-1", "", False),
        ("max_chars", "1.0", "a", False),
        ("max_chars", " 1", "a", False),
    ],
)
def test_automatic_rule(kind: str, value: str, text: str, expected: bool) -> None:
    report = evaluate_acceptance([_rule(kind, value)], text)
    assert report.passed is expected
    assert report.requires_review is False
    assert len(report.checks) == 1
    assert report.checks[0].type == kind
    assert report.checks[0].passed is expected


@pytest.mark.parametrize("kind", [kind.value for kind in AcceptanceType if kind != "human"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_automatic_rule_value_fails(kind: str, value: str | None) -> None:
    report = evaluate_acceptance([_rule(kind, value)], "output")
    assert report.passed is False
    assert report.checks[0].passed is False


def test_empty_rules_accept_automatic_checks() -> None:
    report = evaluate_acceptance([], "output")
    assert report.passed is True
    assert report.requires_review is False
    assert report.checks == []


def test_human_is_pending_without_failing_automatic_acceptance() -> None:
    report = evaluate_acceptance([_rule("human"), _rule("must_contain", "ready")], "ready")
    assert report.passed is True
    assert report.requires_review is True
    assert report.checks[0].passed is False
    assert report.checks[1].passed is True


def test_human_does_not_hide_failed_automatic_rule() -> None:
    report = evaluate_acceptance([_rule("human"), _rule("must_contain", "ready")], "wrong")
    assert report.passed is False
    assert report.requires_review is True


def test_json_schema_accepts_output_and_local_reference() -> None:
    schema = {
        "type": "object",
        "properties": {"count": {"$ref": "#/$defs/count"}},
        "required": ["count"],
        "additionalProperties": False,
        "$defs": {"count": {"type": "integer", "minimum": 1}},
    }
    rule = _rule("json_schema", json.dumps(schema))
    assert evaluate_acceptance([rule], '{"count": 1}').passed is True
    assert evaluate_acceptance([rule], '{"count": 0}').passed is False
    assert evaluate_acceptance([rule], '{"count": "1"}').passed is False


@pytest.mark.parametrize(
    ("schema", "text"),
    [
        ("{", "{}"),
        ('{"type": "invalid"}', "{}"),
        ("{}", "not-json"),
        ("{}", "NaN"),
        ("false", "{}"),
        ('{"$ref": "#/$defs/missing"}', "{}"),
        ('{"$ref": "#"}', "{}"),
    ],
)
def test_invalid_schema_output_or_reference_fails(schema: str, text: str) -> None:
    assert evaluate_acceptance([_rule("json_schema", schema)], text).passed is False


@pytest.mark.parametrize("uri", ["https://example.invalid/schema", "file:///forbidden-schema"])
def test_external_schema_references_never_retrieve_resources(uri: str) -> None:
    # Call the actual isolated evaluator while replacing its retrieval function in
    # a direct check as well, so network isolation is tested rather than assumed.
    from app.core.acceptance import _evaluate_rule

    rule = _rule("json_schema", json.dumps({"$ref": uri}))
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("Network attempted")
    ) as request:
        assert _evaluate_rule(rule, "{}").passed is False
    request.assert_not_called()
    assert evaluate_acceptance([rule], "{}").passed is False


def test_regex_backtracking_is_bounded() -> None:
    report = evaluate_acceptance([_rule("regex", "^(a+)+$")], "a" * 40 + "!")
    assert report.passed is False
    assert report.checks[0].detail == "Acceptance evaluation timed out."


def test_schema_pattern_backtracking_is_bounded() -> None:
    schema = json.dumps({"type": "string", "pattern": "^(a+)+$"})
    report = evaluate_acceptance([_rule("json_schema", schema)], json.dumps("a" * 40 + "!"))
    assert report.passed is False
    assert report.checks[0].detail == "Acceptance evaluation timed out."


def test_timeout_fails_all_automatic_checks_but_preserves_human_requirement() -> None:
    report = evaluate_acceptance(
        [_rule("must_contain", "a"), _rule("regex", "^(a+)+$"), _rule("human")],
        "a" * 40 + "!",
    )
    assert report.passed is False
    assert report.requires_review is True
    assert [check.passed for check in report.checks] == [False, False, False]


def test_check_details_do_not_echo_output_data() -> None:
    output = "private-result-marker"
    report = evaluate_acceptance([_rule("json_schema", '{"type":"integer"}')], json.dumps(output))
    assert output not in report.checks[0].detail
