"""Validate task.v1 and result.v1 payloads against JSON Schema and Pydantic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.core.schema_loader import load_schema
from app.core.schemas import ResultV1, TaskV1

_FORMAT_CHECKER = FormatChecker()

_TASK_VALIDATOR = Draft202012Validator(
    load_schema("task.v1.json"),
    format_checker=_FORMAT_CHECKER,
)
_RESULT_VALIDATOR = Draft202012Validator(
    load_schema("result.v1.json"),
    format_checker=_FORMAT_CHECKER,
)


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    source: str


def _format_jsonschema_errors(
    errors: list[jsonschema.exceptions.ValidationError],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for err in errors:
        path = "/" + "/".join(str(p) for p in err.absolute_path)
        if not path or path == "/":
            path = "$"
        issues.append(ValidationIssue(path=path, message=err.message, source="jsonschema"))
    return issues


def _format_pydantic_errors(exc: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        parts = [str(part) for part in loc]
        path = "$" if not parts else "$/" + "/".join(parts)
        issues.append(ValidationIssue(path=path, message=err["msg"], source="pydantic"))
    return issues


def _dedupe_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.path, issue.message, issue.source)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def validate_task(data: dict[str, Any]) -> list[ValidationIssue]:
    """Return validation issues; empty list means valid."""
    issues: list[ValidationIssue] = []

    json_errors = sorted(_TASK_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path))
    if json_errors:
        issues.extend(_format_jsonschema_errors(json_errors))

    try:
        TaskV1.model_validate(data)
    except ValidationError as exc:
        issues.extend(_format_pydantic_errors(exc))

    return _dedupe_issues(issues)


def validate_result(data: dict[str, Any]) -> list[ValidationIssue]:
    """Return validation issues; empty list means valid."""
    issues: list[ValidationIssue] = []

    json_errors = sorted(_RESULT_VALIDATOR.iter_errors(data), key=lambda e: list(e.absolute_path))
    if json_errors:
        issues.extend(_format_jsonschema_errors(json_errors))

    try:
        ResultV1.model_validate(data)
    except ValidationError as exc:
        issues.extend(_format_pydantic_errors(exc))

    return _dedupe_issues(issues)


def issues_as_strings(issues: list[ValidationIssue]) -> list[str]:
    return [f"{issue.path} ({issue.source}): {issue.message}" for issue in issues]
