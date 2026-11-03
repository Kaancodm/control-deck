"""Unit tests for task.v1 / result.v1 validation and JSON Schema parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.core.schemas import ResultV1, TaskV1
from app.core.validate import validate_result, validate_task

EXAMPLES = Path(__file__).parent / "examples"
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict)
    return data


def _jsonschema_valid(schema_name: str, data: dict[str, Any]) -> bool:
    with (SCHEMAS / schema_name).open(encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft202012Validator(schema)
    return not list(validator.iter_errors(data))


def _pydantic_valid(model: type[TaskV1] | type[ResultV1], data: dict[str, Any]) -> bool:
    try:
        model.model_validate(data)
        return True
    except ValidationError:
        return False


TASK_VALID = sorted(EXAMPLES.glob("task_valid_*.json"))
TASK_INVALID = sorted(EXAMPLES.glob("task_invalid_*.json"))
RESULT_VALID = sorted(EXAMPLES.glob("result_valid_*.json"))
RESULT_INVALID = sorted(EXAMPLES.glob("result_invalid_*.json"))


@pytest.mark.parametrize("path", TASK_VALID, ids=lambda p: p.name)
def test_task_valid_examples(path: Path) -> None:
    data = _load(path)
    assert validate_task(data) == []


@pytest.mark.parametrize("path", TASK_INVALID, ids=lambda p: p.name)
def test_task_invalid_examples(path: Path) -> None:
    data = _load(path)
    assert validate_task(data) != []


@pytest.mark.parametrize("path", RESULT_VALID, ids=lambda p: p.name)
def test_result_valid_examples(path: Path) -> None:
    data = _load(path)
    assert validate_result(data) == []


@pytest.mark.parametrize("path", RESULT_INVALID, ids=lambda p: p.name)
def test_result_invalid_examples(path: Path) -> None:
    data = _load(path)
    assert validate_result(data) != []


@pytest.mark.parametrize("path", TASK_VALID + TASK_INVALID, ids=lambda p: p.name)
def test_task_jsonschema_pydantic_parity(path: Path) -> None:
    data = _load(path)
    js_ok = _jsonschema_valid("task.v1.json", data)
    py_ok = _pydantic_valid(TaskV1, data)
    assert js_ok == py_ok, f"parity mismatch for {path.name}: jsonschema={js_ok} pydantic={py_ok}"


@pytest.mark.parametrize("path", RESULT_VALID + RESULT_INVALID, ids=lambda p: p.name)
def test_result_jsonschema_pydantic_parity(path: Path) -> None:
    data = _load(path)
    js_ok = _jsonschema_valid("result.v1.json", data)
    py_ok = _pydantic_valid(ResultV1, data)
    assert js_ok == py_ok, f"parity mismatch for {path.name}: jsonschema={js_ok} pydantic={py_ok}"


def test_validate_task_reports_field_path() -> None:
    data = _load(TASK_INVALID[0])
    issues = validate_task(data)
    assert any("data_class" in issue.path or issue.path == "$" for issue in issues)


def test_validate_result_failed_requires_error_message() -> None:
    data = _load(EXAMPLES / "result_invalid_01_failed_without_error.json")
    issues = validate_result(data)
    assert issues
    joined = " ".join(f"{i.path}: {i.message}" for i in issues)
    assert "error" in joined.lower() or "failed" in joined.lower()
