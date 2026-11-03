"""Run reference cases against an executor and evaluate expectations."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.core.schemas import DataClass, ResultStatus, TaskKind, TaskV1, Worker
from app.privacy.config import PrivacySettings
from app.privacy.gate import PrivacyInputError
from app.redaction.engine import RedactionError, rehydrate
from app.redaction.payload import LaneNotAllowedError, build_payload
from app.reference.schemas import ReferenceExpect
from app.reference.validate import LoadedReferenceCase


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
    privacy_settings: PrivacySettings = field(
        default_factory=lambda: PrivacySettings(inbox=Path("inbox"), blocklist_configured=True)
    )
    _maps: dict[UUID, dict[str, str]] = field(default_factory=dict, init=False, repr=False)

    def execute(self, task: TaskV1) -> ExecutionResult:
        worker = task.fallback_chain[0]

        if task.data_class == DataClass.LOCAL_ONLY and worker != Worker.OLLAMA:
            return ExecutionResult(
                status=ResultStatus.BLOCKED,
                output_text="",
                worker=worker,
                wall_seconds=self.fixed_wall_seconds,
            )

        try:
            mapping = self._maps.setdefault(task.task_id, {})
            payload = build_payload(
                task,
                lane=worker,
                settings=self.privacy_settings,
                existing=mapping,
                mapping_sink=mapping,
            )
        except (PrivacyInputError, RedactionError, LaneNotAllowedError):
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

        text = self._execute_payload(payload.text, kind=task.kind)
        if task.data_class == DataClass.CLOUD_REDACTED:
            try:
                text = rehydrate(text, task_id=task.task_id, mapping=mapping)
            except RedactionError:
                return ExecutionResult(ResultStatus.FAILED, "", worker, self.fixed_wall_seconds)

        return ExecutionResult(
            status=ResultStatus.SUCCEEDED,
            output_text=text,
            worker=worker,
            wall_seconds=self.fixed_wall_seconds,
        )

    def _execute_payload(self, text: str, *, kind: TaskKind) -> str:
        """The fake worker receives only prepared text, never the original task or map."""
        if kind == TaskKind.SUMMARIZE:
            return "reference-ok: Kurzfassung des Eingabetexts."
        return "reference-ok: Ausfuehrung abgeschlossen."


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
        try:
            matched = re.search(expect.regex, result.output_text) is not None
        except re.error as exc:
            failures.append(
                ExpectCheckFailure(
                    rule="regex",
                    detail=f"invalid pattern at runtime: {exc}",
                )
            )
        else:
            if not matched:
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
        started = time.perf_counter()
        failures: list[ExpectCheckFailure] = []
        try:
            task = TaskV1.model_validate(loaded.task_payload)
            execution = executor.execute(task)
            failures = evaluate_expect(loaded.case.expect, execution)
        except Exception as exc:  # noqa: BLE001 — per-case isolation for reference runs
            failures = [ExpectCheckFailure(rule="execution", detail=f"{type(exc).__name__}: {exc}")]
        duration = time.perf_counter() - started
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
