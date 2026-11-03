"""Evaluate trusted acceptance checks without trusting worker-provided verdicts."""

from __future__ import annotations

import json
import logging
import os
import re
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from app.core.schemas import AcceptanceRule, AcceptanceType, ResultCheck

# Imports have their own budget; regex backtracking and schema recursion remain bounded.
_STARTUP_TIMEOUT_SECONDS = 10.0
_EVALUATION_TIMEOUT_SECONDS = 2.0
_WORKER_PROGRAM = "from app.core.acceptance import _worker_main; _worker_main()"
_WORKER_READY = b"READY\n"
_LOGGER = logging.getLogger(__name__)


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


def _wait_for_worker_ready(process: subprocess.Popen[bytes]) -> None:
    """Read only the fixed readiness marker, bounded even for a partial response."""
    if process.stdout is None:
        raise ValueError("Acceptance evaluator has no output pipe")
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    received = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while len(received) < len(_WORKER_READY):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise subprocess.TimeoutExpired("acceptance startup", _STARTUP_TIMEOUT_SECONDS)
            # Avoid buffered readline(), which can block on an incomplete marker
            # or consume bytes belonging to the JSON response used by communicate().
            chunk = os.read(process.stdout.fileno(), len(_WORKER_READY) - len(received))
            if not chunk:
                raise ValueError("Acceptance evaluator exited before readiness")
            received.extend(chunk)
    if received != _WORKER_READY:
        raise ValueError("Unexpected acceptance evaluator readiness marker")


def _evaluate_isolated(rules: list[AcceptanceRule], text: str) -> list[ResultCheck]:
    payload = json.dumps(
        {"rules": [rule.model_dump(mode="json") for rule in rules], "text": text}
    ).encode("utf-8")
    phase = "startup"
    try:
        with subprocess.Popen(
            [sys.executable, "-c", _WORKER_PROGRAM],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                _wait_for_worker_ready(process)
                phase = "evaluation"
                stdout, _ = process.communicate(input=payload, timeout=_EVALUATION_TIMEOUT_SECONDS)
                if process.returncode != 0:
                    raise ValueError("Acceptance evaluator exited unsuccessfully")
            finally:
                # Popen's context manager closes pipes, but does not kill a stuck
                # child. Reap it before leaving on every error or timeout path.
                if process.poll() is None:
                    process.kill()
                process.wait()
        data = json.loads(stdout)
        checks = [ResultCheck.model_validate(item) for item in data]
        if len(checks) != len(rules):
            raise ValueError("Unexpected acceptance evaluator response")
        return checks
    except subprocess.TimeoutExpired:
        budget = _STARTUP_TIMEOUT_SECONDS if phase == "startup" else _EVALUATION_TIMEOUT_SECONDS
        _LOGGER.warning("Acceptance %s timed out (budget_seconds=%s)", phase, budget)
        if phase == "startup":
            return _failed_checks(rules, "Acceptance evaluator startup timed out.")
        return _failed_checks(rules, "Acceptance evaluation timed out.")
    except (OSError, ValueError, TypeError):
        return _failed_checks(rules, "Acceptance evaluator unavailable.")


def evaluate_acceptance(rules: list[AcceptanceRule], text: str) -> AcceptanceReport:
    """Evaluate rules on output text; ``human`` always requires owner review.

    Regex uses Python search semantics, ``json_schema`` is an inline Draft 2020-12
    schema over JSON output, and ``max_chars`` counts Unicode code points. Batches
    with regex/schema rules get ten seconds for child startup and imports, then
    a separate two-second wall limit for payload transfer and evaluation.
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
    sys.stdout.buffer.write(_WORKER_READY)
    sys.stdout.buffer.flush()
    payload = json.load(sys.stdin)
    rules = [AcceptanceRule.model_validate(rule) for rule in payload["rules"]]
    checks = [_evaluate_rule(rule, payload["text"]) for rule in rules]
    json.dump([check.model_dump(mode="json") for check in checks], sys.stdout)
