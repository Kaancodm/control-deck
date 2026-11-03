"""Tests for reference case loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.reference.load import load_all_cases, load_case_file
from app.reference.validate import (
    load_reference_case_dict,
    materialize_task,
    validate_loaded_case,
    validate_reference_case,
)

CASES_DIR = Path(__file__).resolve().parents[1] / "reference" / "cases"


def test_load_all_dummy_cases() -> None:
    loaded, issues = load_all_cases(CASES_DIR)
    assert not issues
    assert len(loaded) == 4
    assert [item.case.id for item in loaded] == sorted(item.case.id for item in loaded)


def test_materialize_task_adds_ids() -> None:
    path = CASES_DIR / "001-normal-summarize-local.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert isinstance(raw, dict)
    body = raw["task"]
    assert isinstance(body, dict)
    full = materialize_task(body, task_id="550e8400-e29b-41d4-a716-446655440099")
    assert full["task_id"] == "550e8400-e29b-41d4-a716-446655440099"
    assert "created_at" in full
    assert validate_loaded_case(raw) == []


def test_invalid_reference_case_unknown_field() -> None:
    path = CASES_DIR / "001-normal-summarize-local.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert isinstance(raw, dict)
    raw["unexpected"] = True
    assert validate_reference_case(raw) != []


def test_invalid_task_body_after_envelope() -> None:
    path = CASES_DIR / "001-normal-summarize-local.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert isinstance(raw, dict)
    task = raw["task"]
    assert isinstance(task, dict)
    task.pop("data_class")
    assert validate_loaded_case(raw) != []


def test_load_case_file_roundtrip() -> None:
    loaded = load_case_file(CASES_DIR / "002-trap-fake-api-key.yaml")
    assert loaded.case.category.value == "trap"
    assert loaded.case.expect.status.value == "blocked"


def test_load_reference_case_dict_raises_on_invalid() -> None:
    with pytest.raises(ValueError, match="invalid reference case"):
        load_reference_case_dict({"id": "bad", "category": "normal"})


def test_invalid_expect_regex_rejected() -> None:
    path = CASES_DIR / "001-normal-summarize-local.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert isinstance(raw, dict)
    expect = raw["expect"]
    assert isinstance(expect, dict)
    expect["regex"] = "["
    issues = validate_loaded_case(raw)
    assert issues
    joined = " ".join(f"{i.path} {i.message}" for i in issues).lower()
    assert "regex" in joined or "regular expression" in joined
