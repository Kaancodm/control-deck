"""Reference runs exercise only the configured, privacy-gated Ollama fake CLI."""

from __future__ import annotations

import hashlib
import json
import stat
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.schemas import ResultStatus, TaskV1, Worker
from app.redaction.artifacts import PayloadArtifactError
from app.redaction.payload import render_payload
from app.reference import ollama as reference_ollama
from app.reference.ollama import OllamaReferenceExecutor
from app.workers.contracts import AttemptContext
from app.workers.lanes import ollama
from app.workers.lanes.ollama import OllamaSimulation
from scripts.run_reference import main

MODEL = "reference-fixture:configured"


def _task(**updates):
    payload = json.loads((Path(__file__).parent / "examples/task_valid_01.json").read_text())
    payload.update(updates)
    return TaskV1.model_validate(payload)


@pytest.mark.parametrize("arguments", [[], ["--executor", "ollama-fake-cli", "--model", MODEL]])
def test_reference_cli_preserves_all_existing_cases(arguments, capsys):
    assert main(arguments) == 0
    assert "Summary: 4/4 passed, 0 failed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--executor", "ollama-fake-cli"],
        ["--executor", "ollama-fake-cli", "--model", ""],
        ["--executor", "ollama-fake-cli", "--model", " "],
        ["--executor", "ollama-fake-cli", "--model", "../fixture"],
        ["--executor", "ollama-fake-cli", "--model", "fixture:cloud"],
        ["--executor", "ollama-fake-cli", "--model", "fixture-cloud"],
        ["--executor", "fake", "--model", MODEL],
        ["--executor", "ollama", "--model", MODEL],
    ],
)
def test_reference_cli_rejects_missing_unsafe_or_live_configuration(arguments, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid configuration must not execute")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_reference_archives_exact_prepared_bytes_and_restores_output_only_on_host(monkeypatch):
    address = "synthetic" + "@" + "example.invalid"
    task = _task(data_class="cloud_redacted", instructions="Read " + address + "\r\n")
    executor = OllamaReferenceExecutor(model=MODEL)
    executor._executor.simulation = OllamaSimulation(models=(MODEL,), behavior="echo")
    original_execute = executor._executor.execute
    observed_contexts = []
    observed_payloads = []
    original_run = ollama.run_process

    def inspect_context(context):
        assert isinstance(context, AttemptContext)
        assert not any(isinstance(value, (TaskV1, dict)) for value in vars(context).values())
        assert address not in context.payload_text
        assert "[[CD_" in context.payload_text
        assert context.redaction_applied
        assert context.redaction_placeholders == 1
        assert context.model == MODEL
        assert context.timeout_s == task.policy.timeout_s
        assert address not in repr(context)
        artifact = context.run_dir / "payload.sent.md"
        data = artifact.read_bytes()
        assert data == context.payload_text.encode("utf-8")
        assert hashlib.sha256(data).hexdigest() == context.payload_sha256
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
        assert stat.S_IMODE(context.run_dir.stat().st_mode) == 0o700
        observed_contexts.append(context)
        return original_execute(context)

    def inspect_process(command, **kwargs):
        if command[-2:] == ("run", MODEL):
            observed_payloads.append(kwargs["input_bytes"])
        return original_run(command, **kwargs)

    monkeypatch.setattr(executor._executor, "execute", inspect_context)
    monkeypatch.setattr(ollama, "run_process", inspect_process)
    result = executor.execute(task)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.worker == Worker.OLLAMA
    assert result.output_text == render_payload(task, settings=executor.privacy_settings)
    assert len(observed_contexts) == 1
    context = observed_contexts[0]
    assert observed_payloads == [context.payload_text.encode("utf-8")]
    assert not context.run_dir.exists()


@pytest.mark.parametrize(
    ("updates", "worker"),
    [
        ({"data_class": "cloud_ok", "fallback_chain": ["claude", "ollama"]}, Worker.CLAUDE),
        ({"data_class": "cloud_ok", "fallback_chain": ["codex", "ollama"]}, Worker.CODEX),
        ({"fallback_chain": ["claude"], "policy": {"allowed_workers": ["claude"]}}, Worker.OLLAMA),
        ({"policy": {"allowed_workers": []}}, Worker.OLLAMA),
    ],
)
def test_reference_never_substitutes_ollama_for_disallowed_or_other_routing(
    updates, worker, monkeypatch
):
    executor = OllamaReferenceExecutor(model=MODEL)

    def unexpected(*args, **kwargs):
        pytest.fail("Blocked or non-Ollama routing must not execute")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    result = executor.execute(_task(**updates))
    assert result.status == ResultStatus.BLOCKED
    assert result.output_text == ""
    assert result.worker == worker


def test_reference_privacy_gate_runs_before_cli(monkeypatch):
    executor = OllamaReferenceExecutor(model=MODEL)

    def unexpected(*args, **kwargs):
        pytest.fail("Detected credentials must never reach the CLI")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    result = executor.execute(_task(instructions="credential=" + "synthetic-fixture-value"))
    assert result.status == ResultStatus.BLOCKED
    assert result.output_text == ""


@pytest.mark.parametrize("behavior", ["fail", "empty", "invalid_utf8"])
def test_reference_maps_cli_failure_without_partial_output(behavior):
    executor = OllamaReferenceExecutor(model=MODEL)
    executor._executor.simulation = OllamaSimulation(models=(MODEL,), behavior=behavior)
    result = executor.execute(_task())
    assert result.status == ResultStatus.FAILED
    assert result.output_text == ""
    assert result.worker == Worker.OLLAMA


def test_reference_reports_measured_wall_time_including_cli_delay():
    executor = OllamaReferenceExecutor(model=MODEL)
    executor._executor.simulation = OllamaSimulation(
        models=(MODEL,), behavior="delay", delay_s=0.08
    )
    started = time.monotonic()
    result = executor.execute(_task())
    elapsed = time.monotonic() - started
    assert result.status == ResultStatus.SUCCEEDED
    assert 0.08 <= result.wall_seconds <= elapsed
    assert result.wall_seconds != executor.fixed_wall_seconds


def test_reference_maps_archive_failure_without_running_cli(monkeypatch):
    def unavailable(*args, **kwargs):
        raise PayloadArtifactError("artifact_unavailable")

    def unexpected(*args, **kwargs):
        pytest.fail("Unarchived payload must not execute")

    monkeypatch.setattr(reference_ollama, "write_payload_sent", unavailable)
    monkeypatch.setattr(ollama, "run_process", unexpected)
    result = OllamaReferenceExecutor(model=MODEL).execute(_task())
    assert result.status == ResultStatus.FAILED
    assert result.output_text == ""


def test_reference_failed_result_discards_output(monkeypatch):
    executor = OllamaReferenceExecutor(model=MODEL)
    original = executor._executor.execute

    def failed(context):
        result = original(context)
        return result.model_copy(update={"status": ResultStatus.BLOCKED})

    monkeypatch.setattr(executor._executor, "execute", failed)
    result = executor.execute(_task())
    assert result.status == ResultStatus.BLOCKED
    assert result.output_text == ""


def test_reference_rehydration_rejects_unknown_placeholder():
    task = _task(data_class="cloud_redacted")
    executor = OllamaReferenceExecutor(model=MODEL)
    executor._executor.simulation = replace(
        executor._executor.simulation,
        output_text="[[CD_" + task.task_id.hex + "_EMAIL_999]]",
    )
    result = executor.execute(task)
    assert result.status == ResultStatus.FAILED
    assert result.output_text == ""
