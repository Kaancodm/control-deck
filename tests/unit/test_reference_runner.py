"""Tests for reference runner expectation evaluation and fake executor."""

from __future__ import annotations

from pathlib import Path

from app.core.schemas import DataClass, ResultStatus, TaskV1, Worker
from app.reference.load import load_all_cases
from app.reference.runner import (
    CaseNotApplicable,
    CaseOutcome,
    ExecutionResult,
    ExpectCheckFailure,
    FakeExecutor,
    evaluate_expect,
    format_summary,
    run_cases,
)
from app.reference.schemas import ExpectStatus, ReferenceExpect

CASES_DIR = Path(__file__).resolve().parents[1] / "reference" / "cases"


def _expect(**kwargs: object) -> ReferenceExpect:
    defaults: dict[str, object] = {
        "status": ExpectStatus.SUCCEEDED,
        "must_contain": [],
        "must_not_contain": [],
        "regex": None,
        "max_chars": None,
        "max_seconds": None,
        "allowed_workers": [],
    }
    defaults.update(kwargs)
    return ReferenceExpect.model_validate(defaults)


def test_evaluate_status_mismatch() -> None:
    expect = _expect(status=ExpectStatus.SUCCEEDED)
    result = ExecutionResult(
        status=ResultStatus.FAILED,
        output_text="",
        worker=Worker.OLLAMA,
        wall_seconds=0.1,
    )
    failures = evaluate_expect(expect, result)
    assert any(f.rule == "status" for f in failures)


def test_evaluate_must_contain_and_not_contain() -> None:
    expect = _expect(
        must_contain=["hello"],
        must_not_contain=["secret"],
    )
    result = ExecutionResult(
        status=ResultStatus.SUCCEEDED,
        output_text="hello world",
        worker=Worker.OLLAMA,
        wall_seconds=0.1,
    )
    assert evaluate_expect(expect, result) == []

    bad = ExecutionResult(
        status=ResultStatus.SUCCEEDED,
        output_text="hello secret",
        worker=Worker.OLLAMA,
        wall_seconds=0.1,
    )
    failures = evaluate_expect(expect, bad)
    assert any(f.rule == "must_not_contain" for f in failures)


def test_evaluate_regex_max_chars_max_seconds_workers() -> None:
    expect = _expect(
        regex=r"^ok$",
        max_chars=3,
        max_seconds=1.0,
        allowed_workers=[Worker.CLAUDE],
    )
    result = ExecutionResult(
        status=ResultStatus.SUCCEEDED,
        output_text="ok",
        worker=Worker.CLAUDE,
        wall_seconds=0.5,
    )
    assert evaluate_expect(expect, result) == []

    long_text = ExecutionResult(
        status=ResultStatus.SUCCEEDED,
        output_text="too long",
        worker=Worker.OLLAMA,
        wall_seconds=2.0,
    )
    failures = evaluate_expect(expect, long_text)
    rules = {f.rule for f in failures}
    assert "regex" in rules
    assert "max_chars" in rules
    assert "max_seconds" in rules
    assert "allowed_workers" in rules


def test_fake_executor_and_run_cases_integration() -> None:
    cases, _issues = load_all_cases(CASES_DIR)
    summary = run_cases(cases, FakeExecutor())
    assert summary.total == 14
    assert summary.failed == 0
    assert summary.skipped == 0
    assert all(r.outcome == CaseOutcome.PASS for r in summary.results)


def test_run_cases_counts_not_applicable_as_skipped_without_failing() -> None:
    cases, _issues = load_all_cases(CASES_DIR)

    class LocalOnlyExecutor(FakeExecutor):
        def execute(self, task: TaskV1) -> ExecutionResult:
            if task.data_class != DataClass.LOCAL_ONLY:
                raise CaseNotApplicable("cloud lane not available on this executor")
            return super().execute(task)

    summary = run_cases(cases, LocalOnlyExecutor())
    skipped = [r for r in summary.results if r.outcome == CaseOutcome.SKIP]
    assert {r.case_id[:3] for r in skipped} == {"002", "007", "008", "010", "013", "014"}
    assert all(r.failures == [] for r in skipped)
    assert summary.failed == 0
    assert summary.skipped == 6
    assert summary.passed == 8
    text = format_summary(summary)
    assert "[SKIP] 002-trap-fake-api-key (trap)" in text
    assert "  - skipped: cloud lane not available on this executor" in text
    assert text.endswith("Summary: 8/14 passed, 0 failed, 6 skipped")


def test_fake_executor_blocked_on_trap_pattern() -> None:
    cases, _issues = load_all_cases(CASES_DIR)
    trap = next(c for c in cases if c.case.id.startswith("002"))
    task = TaskV1.model_validate(trap.task_payload)
    result = FakeExecutor().execute(task)
    assert result.status == ResultStatus.BLOCKED


def test_evaluate_expect_empty_failures_means_pass() -> None:
    expect = _expect(must_contain=["x"])
    result = ExecutionResult(
        status=ResultStatus.SUCCEEDED,
        output_text="x",
        worker=Worker.OLLAMA,
        wall_seconds=0.0,
    )
    assert evaluate_expect(expect, result) == []


def test_run_cases_catches_executor_exception() -> None:
    cases, _issues = load_all_cases(CASES_DIR)

    class BrokenExecutor:
        def execute(self, task: TaskV1) -> ExecutionResult:
            raise RuntimeError("simulated worker failure")

    summary = run_cases(cases, BrokenExecutor())  # type: ignore[arg-type]
    assert summary.failed == summary.total
    assert all(r.failures[0].rule == "execution" for r in summary.results)


def test_failure_detail_shape() -> None:
    failures = evaluate_expect(
        _expect(status=ExpectStatus.FAILED),
        ExecutionResult(
            status=ResultStatus.SUCCEEDED,
            output_text="",
            worker=Worker.OLLAMA,
            wall_seconds=0.0,
        ),
    )
    assert failures == [ExpectCheckFailure(rule="status", detail="expected failed, got succeeded")]
