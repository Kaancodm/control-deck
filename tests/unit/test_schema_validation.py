"""Unit tests for task.v1 / result.v1 validation and JSON Schema parity."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.core.schema_loader import load_schema
from app.core.schemas import ResultV1, TaskV1
from app.core.validate import validate_result, validate_task

EXAMPLES = Path(__file__).parent / "examples"
_FORMAT_CHECKER = FormatChecker()


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict)
    return data


def _jsonschema_valid(schema_name: str, data: dict[str, Any]) -> bool:
    validator = Draft202012Validator(
        load_schema(schema_name),
        format_checker=_FORMAT_CHECKER,
    )
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


def test_load_schema_without_source_checkout_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schemas load from the installed package, not a repo-relative checkout path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    schema = load_schema("task.v1.json")
    assert schema["properties"]["schema_version"]["const"] == "task.v1"


def test_wheel_packages_json_schemas(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(wheel_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable: {build.stderr.strip()}")
    wheels = list(wheel_dir.glob("control_deck-*.whl"))
    assert wheels, "expected a built wheel"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
    assert any(name.endswith("app/schemas/task.v1.json") for name in names)
    assert any(name.endswith("app/schemas/result.v1.json") for name in names)


@pytest.mark.parametrize(
    ("payload", "field_path"),
    [
        ({"created_at": "2026-01-15T08:00:00"}, "$/created_at"),
        (
            {
                "timing": {
                    "started_at": "2026-01-15T08:00:00",
                    "finished_at": "2026-01-15T08:01:00",
                }
            },
            "$/timing/started_at",
        ),
    ],
)
def test_naive_datetimes_rejected(payload: dict[str, Any], field_path: str) -> None:
    if "created_at" in payload:
        task = _load(TASK_VALID[0])
        task["created_at"] = payload["created_at"]
        issues = validate_task(task)
    else:
        result = _load(RESULT_VALID[0])
        result["timing"] = payload["timing"]
        issues = validate_result(result)

    assert issues
    assert any(issue.path == field_path for issue in issues)


def test_payload_hash_trailing_newline_rejected() -> None:
    result = _load(RESULT_VALID[0])
    result["payload_hash"] = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
    issues = validate_result(result)
    assert issues
    sources = {issue.source for issue in issues}
    assert "jsonschema" in sources
    assert "pydantic" in sources


def test_usage_null_tokens_rejected() -> None:
    result = _load(RESULT_VALID[0])
    result["usage"]["input_tokens"] = None
    issues = validate_result(result)
    assert issues
    assert any("input_tokens" in issue.path for issue in issues)


def _result_json_roundtrip(model: ResultV1) -> dict[str, Any]:
    """Serialize like production: omit unset usage token fields, keep required nulls."""
    payload = model.model_dump(mode="json")
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens"):
            if usage.get(key) is None:
                usage.pop(key, None)
    return payload


@pytest.mark.parametrize("path", RESULT_VALID, ids=lambda p: p.name)
def test_result_roundtrip_validate_dump_validate(path: Path) -> None:
    data = _load(path)
    assert validate_result(data) == []
    model = ResultV1.model_validate(data)
    roundtrip = _result_json_roundtrip(model)
    assert validate_result(roundtrip) == []
