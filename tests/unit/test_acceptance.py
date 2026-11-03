"""Core acceptance checks are authoritative and fail closed on invalid inputs."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any
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


def test_slow_startup_does_not_consume_evaluation_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import acceptance

    monkeypatch.setattr(acceptance, "_EVALUATION_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(
        acceptance,
        "_WORKER_PROGRAM",
        "import time; time.sleep(0.75); "
        "from app.core.acceptance import _worker_main; _worker_main()",
    )

    report = evaluate_acceptance([_rule("regex", "^ready$")], "ready")

    assert report.passed is True
    assert report.checks[0].detail == "Acceptance rule passed."


@pytest.fixture
def evaluator_children(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    from app.core import acceptance

    children: list[subprocess.Popen[bytes]] = []
    original_popen = acceptance.subprocess.Popen

    def tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(acceptance.subprocess, "Popen", tracked_popen)
    return children


def _assert_children_reaped(children: list[subprocess.Popen[bytes]]) -> None:
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdin is not None and child.stdin.closed
    assert child.stdout is not None and child.stdout.closed


@pytest.mark.parametrize(
    "program",
    [
        "import time; time.sleep(60)",
        "import sys, time; sys.stdout.write('R'); sys.stdout.flush(); time.sleep(60)",
    ],
)
def test_startup_timeout_is_bounded_logged_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    evaluator_children: list[subprocess.Popen[bytes]],
    program: str,
) -> None:
    from app.core import acceptance

    monkeypatch.setattr(acceptance, "_STARTUP_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(acceptance, "_WORKER_PROGRAM", program)
    with caplog.at_level(logging.WARNING, logger="app.core.acceptance"):
        report = evaluate_acceptance([_rule("regex", "private-rule-marker")], "private-output")

    assert report.passed is False
    assert report.checks[0].detail == "Acceptance evaluator startup timed out."
    assert "Acceptance startup timed out" in caplog.text
    assert "private-rule-marker" not in caplog.text
    assert "private-output" not in caplog.text
    _assert_children_reaped(evaluator_children)


def test_evaluation_timeout_with_full_pipes_is_logged_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    evaluator_children: list[subprocess.Popen[bytes]],
) -> None:
    from app.core import acceptance

    monkeypatch.setattr(acceptance, "_EVALUATION_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(
        acceptance,
        "_WORKER_PROGRAM",
        "import sys, time; "
        "sys.stderr.write('private-stderr' * 100000); sys.stderr.flush(); "
        "print('READY', flush=True); time.sleep(60)",
    )
    with caplog.at_level(logging.WARNING, logger="app.core.acceptance"):
        report = evaluate_acceptance(
            [_rule("regex", "private-rule-marker"), _rule("human")], "private-output" * 100000
        )

    assert report.passed is False
    assert report.requires_review is True
    assert report.checks[0].detail == "Acceptance evaluation timed out."
    assert "Acceptance evaluation timed out" in caplog.text
    for marker in ("private-rule-marker", "private-output", "private-stderr"):
        assert marker not in caplog.text
    _assert_children_reaped(evaluator_children)


@pytest.mark.parametrize(
    "program",
    [
        "print('INVALID', flush=True)",
        "print('READY', flush=True); print('invalid-json', flush=True)",
    ],
)
def test_invalid_worker_protocol_fails_closed_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
    evaluator_children: list[subprocess.Popen[bytes]],
    program: str,
) -> None:
    from app.core import acceptance

    monkeypatch.setattr(acceptance, "_WORKER_PROGRAM", program)

    report = evaluate_acceptance([_rule("regex", "ready")], "ready")

    assert report.passed is False
    assert report.checks[0].detail == "Acceptance evaluator unavailable."
    _assert_children_reaped(evaluator_children)


def test_child_environment_is_allowlisted_and_session_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import acceptance

    for name in ("DECK_OWNER_TOKEN", "DECK_SECRET_KEY", "DECK_DATABASE_URL"):
        monkeypatch.setenv(name, "must-not-leak")
    captured: dict[str, Any] = {}
    original_popen = acceptance.subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        captured.update(kwargs)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(acceptance.subprocess, "Popen", recording_popen)
    report = evaluate_acceptance([_rule("regex", r"^ok$")], "ok")
    assert report.passed
    env = captured["env"]
    assert not any(name.startswith("DECK_") for name in env)
    assert "must-not-leak" not in env.values()
    assert set(env) <= {"PATH", "LANG", "LC_ALL", "PYTHONPATH"}
    assert captured["start_new_session"] is True
    assert captured["cwd"] != os.getcwd()


def test_real_child_never_sees_api_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import acceptance

    monkeypatch.setenv("DECK_OWNER_TOKEN", "must-not-leak")
    monkeypatch.setattr(
        acceptance,
        "_WORKER_PROGRAM",
        "import os,sys;sys.stdout.buffer.write(b'READY\\n');sys.stdout.buffer.flush();"
        "sys.stdin.read();print(__import__('json').dumps("
        "[{'type':'regex','passed':'DECK_OWNER_TOKEN' not in os.environ,'detail':''}]))",
    )
    report = evaluate_acceptance([_rule("regex", "x")], "x")
    assert report.checks[0].passed is True
