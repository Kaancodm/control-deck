"""Run reference cases against an executor and evaluate expectations."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.core.schemas import ResultStatus, TaskV1, Worker
from app.reference.schemas import ReferenceExpect
from app.reference.validate import LoadedReferenceCase

_FAKE_KEY_PATTERN = re.compile(r"sk-TEST-[A-Za-z0-9_-]+")


class CaseOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class ExecutionResult:
    status: ResultStatus
    output_text: str
    worker: Worker
    wall_seconds: float


class Executor(Protocol):
    def execute(self, task: TaskV1) -> ExecutionResult:
        """Run a validated task and return execution outcome."""
        ...


@dataclass
class FakeExecutor:
    """Deterministic stub executor for tests and local reference runs."""

    fixed_wall_seconds: float = 0.01

    def execute(self, task: TaskV1) -> ExecutionResult:
        worker = task.fallback_chain[0]
        blob = _task_text_blob(task)

        if _FAKE_KEY_PATTERN.search(blob):
            return ExecutionResult(
                status=ResultStatus.BLOCKED,
                output_text="",
                worker=worker,
                wall_seconds=self.fixed_wall_seconds,
            )

        if _is_empty_input(task):
            return ExecutionResult(
                status=ResultStatus.FAILED,
                output_text="",
                worker=worker,
                wall_seconds=self.fixed_wall_seconds,
            )

        if task.kind.value == "summarize":
            text = "reference-ok: Kurzfassung des Eingabetexts."
        else:
            text = "reference-ok: Ausfuehrung abgeschlossen."

        return ExecutionResult(
            status=ResultStatus.SUCCEEDED,
            output_text=text,
            worker=worker,
            wall_seconds=self.fixed_wall_seconds,
        )


def _task_text_blob(task: TaskV1) -> str:
    parts = [task.instructions, task.title]
    for inp in task.inputs:
        if inp.value is not None:
            parts.append(inp.value)
    return "\n".join(parts)


def _is_empty_input(task: TaskV1) -> bool:
    if not task.inputs:
        return True
    for inp in task.inputs:
        if inp.type.value == "text" and (inp.value or "").strip():
            return False
        if inp.type.value == "file":
            return False
    return True


@dataclass
class ExpectCheckFailure:
    rule: str
    detail: str


def evaluate_expect(
    expect: ReferenceExpect,
    result: ExecutionResult,
) -> list[ExpectCheckFailure]:
    failures: list[ExpectCheckFailure] = []

    expected_status = ResultStatus(expect.status.value)
    if result.status != expected_status:
        failures.append(
            ExpectCheckFailure(
                rule="status",
                detail=f"expected {expected_status.value}, got {result.status.value}",
            )
        )

    for needle in expect.must_contain:
        if needle not in result.output_text:
            failures.append(
                ExpectCheckFailure(
                    rule="must_contain",
                    detail=f"missing substring: {needle!r}",
                )
            )

    for needle in expect.must_not_contain:
        if needle in result.output_text:
            failures.append(
                ExpectCheckFailure(
                    rule="must_not_contain",
                    detail=f"forbidden substring present: {needle!r}",
                )
            )

    if expect.regex is not None:
        if not re.search(expect.regex, result.output_text):
            failures.append(
                ExpectCheckFailure(
                    rule="regex",
                    detail=f"output does not match pattern: {expect.regex!r}",
                )
            )

    if expect.max_chars is not None and len(result.output_text) > expect.max_chars:
        failures.append(
            ExpectCheckFailure(
                rule="max_chars",
                detail=f"output length {len(result.output_text)} exceeds {expect.max_chars}",
            )
        )

    if expect.max_seconds is not None and result.wall_seconds > expect.max_seconds:
        failures.append(
            ExpectCheckFailure(
                rule="max_seconds",
                detail=(f"wall_seconds {result.wall_seconds} exceeds {expect.max_seconds}"),
            )
        )

    if expect.allowed_workers and result.worker not in expect.allowed_workers:
        failures.append(
            ExpectCheckFailure(
                rule="allowed_workers",
                detail=(
                    f"worker {result.worker.value} not in "
                    f"{[w.value for w in expect.allowed_workers]}"
                ),
            )
        )

    return failures


@dataclass
class CaseRunResult:
    case_id: str
    category: str
    outcome: CaseOutcome
    duration_seconds: float
    failures: list[ExpectCheckFailure] = field(default_factory=list)


@dataclass
class RunSummary:
    results: list[CaseRunResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.outcome == CaseOutcome.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == CaseOutcome.FAIL)

    @property
    def total(self) -> int:
        return len(self.results)


def run_cases(
    cases: list[LoadedReferenceCase],
    executor: Executor,
) -> RunSummary:
    results: list[CaseRunResult] = []
    for loaded in cases:
        task = TaskV1.model_validate(loaded.task_payload)
        started = time.perf_counter()
        execution = executor.execute(task)
        duration = time.perf_counter() - started
        failures = evaluate_expect(loaded.case.expect, execution)
        outcome = CaseOutcome.PASS if not failures else CaseOutcome.FAIL
        results.append(
            CaseRunResult(
                case_id=loaded.case.id,
                category=loaded.case.category.value,
                outcome=outcome,
                duration_seconds=duration,
                failures=failures,
            )
        )
    return RunSummary(results=results)


def format_summary(summary: RunSummary) -> str:
    lines: list[str] = []
    for result in summary.results:
        status_label = result.outcome.value.upper()
        lines.append(
            f"[{status_label}] {result.case_id} ({result.category}) {result.duration_seconds:.3f}s"
        )
        for failure in result.failures:
            lines.append(f"  - {failure.rule}: {failure.detail}")
    lines.append(f"Summary: {summary.passed}/{summary.total} passed, {summary.failed} failed")
    return "\n".join(lines)
