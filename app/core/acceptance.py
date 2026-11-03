"""Evaluate trusted acceptance checks without trusting worker-provided verdicts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from app.core.schemas import AcceptanceRule, AcceptanceType, ResultCheck

# Regex backtracking and schema recursion must not hold an API worker indefinitely.
_EVALUATION_TIMEOUT_SECONDS = 2.0
_WORKER_PROGRAM = "from app.core.acceptance import _worker_main; _worker_main()"


@dataclass(frozen=True)
class AcceptanceReport:
    """Automatic acceptance verdict and the separate need for owner review."""

    checks: list[ResultCheck]
    passed: bool
    requires_review: bool


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant: {value}")


def _evaluate_rule(rule: AcceptanceRule, text: str) -> ResultCheck:
    if rule.type == AcceptanceType.HUMAN:
        return ResultCheck(type=rule.type.value, passed=False, detail="Owner review pending.")
    if rule.value is None or rule.value == "":
        return ResultCheck(type=rule.type.value, passed=False, detail="Rule requires a value.")

    try:
        if rule.type == AcceptanceType.MUST_CONTAIN:
            passed = rule.value in text
        elif rule.type == AcceptanceType.MUST_NOT_CONTAIN:
            passed = rule.value not in text
        elif rule.type == AcceptanceType.REGEX:
            passed = re.search(rule.value, text) is not None
        elif rule.type == AcceptanceType.MAX_CHARS:
            if re.fullmatch(r"[0-9]+", rule.value) is None:
                raise ValueError("max_chars requires a non-negative decimal integer")
            passed = len(text) <= int(rule.value)
        elif rule.type == AcceptanceType.JSON_SCHEMA:
            schema = json.loads(rule.value, parse_constant=_reject_json_constant)
            instance = json.loads(text, parse_constant=_reject_json_constant)
            Draft202012Validator.check_schema(schema)
            # An explicit empty Registry defaults to refusing all retrieval.
            # Omitting it would enable jsonschema's remote-retrieval fallback.
            registry: Registry[Any] = Registry()
            validator = Draft202012Validator(
                schema, registry=registry, format_checker=FormatChecker()
            )
            passed = validator.is_valid(instance)
        else:
            raise ValueError("Unknown acceptance rule")
    except Exception:  # noqa: BLE001 — malformed rules fail closed, without echoing task data
        return ResultCheck(
            type=rule.type.value, passed=False, detail="Invalid rule or output for this rule."
        )
    return ResultCheck(
        type=rule.type.value,
        passed=passed,
        detail="Acceptance rule passed." if passed else "Acceptance rule failed.",
    )


def _failed_checks(rules: list[AcceptanceRule], detail: str) -> list[ResultCheck]:
    return [
        _evaluate_rule(rule, "")
        if rule.type == AcceptanceType.HUMAN
        else ResultCheck(type=rule.type.value, passed=False, detail=detail)
        for rule in rules
    ]


def _evaluate_isolated(rules: list[AcceptanceRule], text: str) -> list[ResultCheck]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _WORKER_PROGRAM],
            input=json.dumps(
                {"rules": [rule.model_dump(mode="json") for rule in rules], "text": text}
            ),
            capture_output=True,
            text=True,
            timeout=_EVALUATION_TIMEOUT_SECONDS,
            check=True,
        )
        data = json.loads(completed.stdout)
        checks = [ResultCheck.model_validate(item) for item in data]
        if len(checks) != len(rules):
            raise ValueError("Unexpected acceptance evaluator response")
        return checks
    except subprocess.TimeoutExpired:
        return _failed_checks(rules, "Acceptance evaluation timed out.")
    except (OSError, subprocess.CalledProcessError, ValueError, TypeError):
        return _failed_checks(rules, "Acceptance evaluator unavailable.")


def evaluate_acceptance(rules: list[AcceptanceRule], text: str) -> AcceptanceReport:
    """Evaluate rules on output text; ``human`` always requires owner review.

    Regex uses Python search semantics, ``json_schema`` is an inline Draft 2020-12
    schema over JSON output, and ``max_chars`` counts Unicode code points. Batches
    with regex/schema rules run in a child process with a two-second wall limit.
    No shell, task code, files, or remote schema resources are evaluated.
    """
    if any(rule.type in (AcceptanceType.REGEX, AcceptanceType.JSON_SCHEMA) for rule in rules):
        checks = _evaluate_isolated(rules, text)
    else:
        checks = [_evaluate_rule(rule, text) for rule in rules]
    return AcceptanceReport(
        checks=checks,
        passed=all(
            check.passed
            for rule, check in zip(rules, checks, strict=True)
            if rule.type != AcceptanceType.HUMAN
        ),
        requires_review=any(rule.type == AcceptanceType.HUMAN for rule in rules),
    )


def _worker_main() -> None:
    """Private bounded-process entry point; emit only check metadata."""
    payload = json.load(sys.stdin)
    rules = [AcceptanceRule.model_validate(rule) for rule in payload["rules"]]
    checks = [_evaluate_rule(rule, payload["text"]) for rule in rules]
    json.dump([check.model_dump(mode="json") for check in checks], sys.stdout)
