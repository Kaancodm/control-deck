"""The fake worker boundary uses prepared text and restores it only locally."""

from uuid import uuid4

from app.core.schemas import ResultStatus, TaskKind, TaskV1
from app.redaction.payload import render_payload
from app.reference.runner import FakeExecutor
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
