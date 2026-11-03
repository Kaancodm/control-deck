"""Validate task.v1 and result.v1 payloads against JSON Schema and Pydantic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import jsonschema
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.core.schemas import ResultV1, TaskV1

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    source: str


def _load_schema(name: str) -> dict[str, Any]:
    path = _SCHEMAS_DIR / name
    with path.open(encoding="utf-8") as handle:
        return cast(dict[str, Any], json.load(handle))


_TASK_VALIDATOR = Draft202012Validator(_load_schema("task.v1.json"))
_RESULT_VALIDATOR = Draft202012Validator(_load_schema("result.v1.json"))


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
