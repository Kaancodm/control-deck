"""Validate reference_case.v1 payloads and embedded tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.core.validate import (
    ValidationIssue,
    _dedupe_issues,
    _format_pydantic_errors,
    validate_task,
)
from app.core.validate import _format_jsonschema_errors as format_jsonschema_errors
from app.reference.schemas import ReferenceCaseV1, ReferenceTaskBody

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    path = _SCHEMAS_DIR / name
    with path.open(encoding="utf-8") as handle:
        return cast(dict[str, Any], json.load(handle))


_REFERENCE_VALIDATOR = Draft202012Validator(_load_schema("reference_case.v1.json"))


def validate_reference_case(data: dict[str, Any]) -> list[ValidationIssue]:
    """Return validation issues; empty list means valid reference case envelope."""
    issues: list[ValidationIssue] = []

    json_errors = sorted(
        _REFERENCE_VALIDATOR.iter_errors(data),
        key=lambda e: list(e.absolute_path),
    )
    if json_errors:
        issues.extend(format_jsonschema_errors(json_errors))

    try:
        ReferenceCaseV1.model_validate(data)
    except ValidationError as exc:
        issues.extend(_format_pydantic_errors(exc))

    return _dedupe_issues(issues)


@dataclass(frozen=True)
class LoadedReferenceCase:
    case: ReferenceCaseV1
    task_payload: dict[str, Any]


def task_body_to_dict(task_body: ReferenceTaskBody | dict[str, Any]) -> dict[str, Any]:
    """Serialize reference task body for task.v1 validation (null parent preserved)."""
    if isinstance(task_body, ReferenceTaskBody):
        data = task_body.model_dump(mode="json", exclude_none=True)
        data["inputs"] = [
            inp.model_dump(mode="json", exclude_none=True) for inp in task_body.inputs
        ]
        if task_body.parent_task_id is None:
            data["parent_task_id"] = None
        return data
    return dict(task_body)


def materialize_task(
    task_body: ReferenceTaskBody | dict[str, Any],
    *,
    task_id: UUID | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    """Build a full task.v1 dict from a reference task body."""
    full = task_body_to_dict(task_body)
    full["task_id"] = str(task_id or uuid4())
    full["created_at"] = created_at
    return full


def validate_loaded_case(data: dict[str, Any]) -> list[ValidationIssue]:
    """Validate reference case envelope and materialized task.v1."""
    issues = validate_reference_case(data)
    if issues:
        return issues

    case = ReferenceCaseV1.model_validate(data)
    task_payload = materialize_task(case.task)
    issues.extend(validate_task(task_payload))
    return _dedupe_issues(issues)


def load_reference_case_dict(data: dict[str, Any]) -> LoadedReferenceCase:
    """Parse and validate a reference case; raises ValueError on failure."""
    issues = validate_loaded_case(data)
    if issues:
        messages = "; ".join(f"{i.path}: {i.message}" for i in issues)
        raise ValueError(f"invalid reference case: {messages}")

    case = ReferenceCaseV1.model_validate(data)
    task_payload = materialize_task(case.task)
    return LoadedReferenceCase(case=case, task_payload=task_payload)
