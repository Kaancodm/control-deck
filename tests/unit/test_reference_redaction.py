"""The fake worker boundary uses prepared text and restores it only locally."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.schemas import ResultStatus, TaskKind, TaskV1
from app.redaction import payload as payload_module
from app.redaction.engine import RedactionOutcome
from app.redaction.payload import PayloadBuild, render_payload
from app.reference import runner
from app.reference.load import load_all_cases
from app.reference.ollama import OllamaReferenceExecutor
from app.reference.runner import CaseOutcome, FakeExecutor, run_cases
from tests.integration.test_database import _minimal_task_payload


def _task() -> TaskV1:
    payload = _minimal_task_payload(uuid4())
    payload["data_class"] = "cloud_redacted"
    payload["instructions"] = "synthetic" + "@" + "example.invalid"
    payload["inputs"] = [{"type": "text", "name": "brief", "value": "ordinary input"}]
    return TaskV1.model_validate(payload)


def test_reference_worker_receives_only_redacted_text_and_host_restores_output() -> None:
    task = _task()

    class Echo(FakeExecutor):
        def _execute_payload(self, text: str, *, kind: TaskKind) -> str:
            assert task.instructions not in text
            assert "[[CD_" in text
            return text

    executor = Echo()
    first = executor.execute(task)
    second = executor.execute(task)
    assert first.status == ResultStatus.SUCCEEDED
    assert first.output_text == render_payload(task, settings=executor.privacy_settings)
    assert second.output_text == first.output_text
    assert task.instructions not in repr(executor)


def test_reference_worker_unknown_token_fails_without_returning_output() -> None:
    class UnknownToken(FakeExecutor):
        def _execute_payload(self, text: str, *, kind: TaskKind) -> str:
            return "[[CD_" + uuid4().hex + "_EMAIL_1]]"

    result = UnknownToken().execute(_task())
    assert result.status == ResultStatus.FAILED
    assert result.output_text == ""


def _case(prefix):
    cases, issues = load_all_cases(Path(__file__).parents[1] / "reference" / "cases")
    assert issues == []
    return next(case for case in cases if case.case.id.startswith(prefix))


@pytest.mark.parametrize("executor_type", [FakeExecutor, OllamaReferenceExecutor])
def test_reference_default_output_depends_on_worker_input(executor_type):
    loaded = _case("001")
    executor = (
        executor_type(model="reference-fixture:configured")
        if executor_type is OllamaReferenceExecutor
        else executor_type()
    )
    outputs = []
    for marker in ("synthetic-first-input", "synthetic-second-input"):
        task = TaskV1.model_validate(loaded.task_payload)
        task.inputs[0].value = marker
        result = executor.execute(task)
        assert result.status == ResultStatus.SUCCEEDED
        assert marker in result.output_text
        outputs.append(result.output_text)
    assert outputs[0] != outputs[1]


@pytest.mark.parametrize("prefix", ["007", "013"])
@pytest.mark.parametrize("constant_worker", [False, True])
def test_reference_privacy_expectations_fail_when_redaction_is_bypassed(
    prefix, constant_worker, monkeypatch
):
    loaded = _case(prefix)
    executor = FakeExecutor()
    if constant_worker:
        # Transport checks must still catch a leak even when a worker omits the PII.
        monkeypatch.setattr(executor, "_execute_payload", lambda *args, **kwargs: "reference-ok")
    assert run_cases([loaded], executor).passed == 1

    def no_redaction(text, **kwargs):
        return RedactionOutcome(text, {}, {}, False)

    monkeypatch.setattr(payload_module, "redact", no_redaction)
    summary = run_cases([loaded], executor)
    assert summary.failed == 1
    assert summary.results[0].outcome == CaseOutcome.FAIL
    assert any(failure.rule == "must_not_contain" for failure in summary.results[0].failures)


def test_reference_private_key_trap_never_reaches_worker(monkeypatch):
    loaded = _case("010")
    executor = FakeExecutor()

    def unexpected(*args, **kwargs):
        pytest.fail("The private-key trap must be blocked before worker execution")

    monkeypatch.setattr(executor, "_execute_prepared", unexpected)
    assert loaded.case.expect.must_not_contain
    assert run_cases([loaded], executor).passed == 1


def test_reference_private_key_trap_catches_bypassed_gate(monkeypatch):
    loaded = _case("010")
    executor = FakeExecutor()
    assert run_cases([loaded], executor).passed == 1

    def unguarded_payload(task, **kwargs):
        text = "\n".join(item.value or "" for item in task.inputs)
        return PayloadBuild(text, "0" * 64, False, 0)

    monkeypatch.setattr(runner, "build_payload", unguarded_payload)
    summary = run_cases([loaded], executor)
    assert summary.failed == 1
    assert {failure.rule for failure in summary.results[0].failures} >= {
        "status",
        "must_not_contain",
    }
