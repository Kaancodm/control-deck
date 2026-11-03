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
from app.reference.load import load_all_cases
from app.reference.ollama import OllamaReferenceExecutor
from app.reference.runner import CaseNotApplicable, CaseOutcome, format_summary, run_cases
from app.workers.contracts import AttemptContext
from app.workers.lanes import ollama
from app.workers.lanes.ollama import OllamaSimulation
from scripts.run_reference import main

MODEL = "reference-fixture:configured"
CASES_DIR = Path(__file__).resolve().parents[1] / "reference" / "cases"


def _task(**updates):
    payload = json.loads((Path(__file__).parent / "examples/task_valid_01.json").read_text())
    payload.update(updates)
    return TaskV1.model_validate(payload)


@pytest.mark.parametrize(
    ("arguments", "summary"),
    [
        ([], "Summary: 14/14 passed, 0 failed, 0 skipped"),
        (
            ["--executor", "ollama-fake-cli", "--model", MODEL],
            "Summary: 10/14 passed, 0 failed, 4 skipped",
        ),
    ],
)
def test_reference_cli_preserves_all_existing_cases(arguments, summary, capsys):
    assert main(arguments) == 0
    assert summary in capsys.readouterr().out


def test_reference_cli_counts_cloud_cases_as_skipped_and_keeps_traps_blocked(capsys):
    assert main(["--executor", "ollama-fake-cli", "--model", MODEL]) == 0
    out = capsys.readouterr().out
    assert "[SKIP] 007-normal-summarize-redacted-pii (normal)" in out
    assert "[SKIP] 008-normal-research-cloud-ok (normal)" in out
    assert "[PASS] 002-trap-fake-api-key (trap)" in out
    assert "[PASS] 010-trap-private-key-cloud-redacted (trap)" in out
    assert "[PASS] 011-trap-token-in-local-only (trap)" in out
    assert "[FAIL]" not in out


def test_reference_run_cases_skips_cloud_routed_cases_only(monkeypatch):
    cases, issues = load_all_cases(CASES_DIR)
    assert issues == []
    executed_models = []
    original_run = ollama.run_process

    def inspect_process(command, **kwargs):
        if command[-2:] == ("run", MODEL):
            executed_models.append(command[-1])
        return original_run(command, **kwargs)

    monkeypatch.setattr(ollama, "run_process", inspect_process)
    summary = run_cases(cases, OllamaReferenceExecutor(model=MODEL))
    outcomes = {result.case_id: result.outcome for result in summary.results}
    skipped = sorted(
        case_id for case_id, outcome in outcomes.items() if outcome == CaseOutcome.SKIP
    )
    assert skipped == [
        "007-normal-summarize-redacted-pii",
        "008-normal-research-cloud-ok",
        "013-normal-draft-redacted-phone",
        "014-normal-review-cloud-ok-codex",
    ]
    assert outcomes["002-trap-fake-api-key"] == CaseOutcome.PASS
    assert outcomes["010-trap-private-key-cloud-redacted"] == CaseOutcome.PASS
    assert outcomes["011-trap-token-in-local-only"] == CaseOutcome.PASS
    assert summary.failed == 0
    assert summary.skipped == 4
    assert summary.passed == summary.total - 4
    # Only locally routed, gate-approved cases reach the fake CLI: 001, 004, 005, 006, 009.
    assert len(executed_models) == 5
    text = format_summary(summary)
    assert "[SKIP] 007-normal-summarize-redacted-pii (normal)" in text
    assert "  - skipped: " in text
    assert text.endswith("Summary: 10/14 passed, 0 failed, 4 skipped")


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
    "updates",
    [
        {"data_class": "cloud_ok", "fallback_chain": ["claude", "ollama"]},
        {"data_class": "cloud_ok", "fallback_chain": ["codex", "ollama"]},
        {"data_class": "cloud_redacted", "fallback_chain": ["claude", "ollama"]},
    ],
)
def test_reference_never_substitutes_ollama_for_cloud_routing(updates, monkeypatch):
    executor = OllamaReferenceExecutor(model=MODEL)

    def unexpected(*args, **kwargs):
        pytest.fail("Non-Ollama routing must not execute")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    with pytest.raises(CaseNotApplicable):
        executor.execute(_task(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"fallback_chain": ["claude"], "policy": {"allowed_workers": ["claude"]}},
        {"policy": {"allowed_workers": []}},
    ],
)
def test_reference_blocks_disallowed_routing_before_cli(updates, monkeypatch):
    executor = OllamaReferenceExecutor(model=MODEL)

    def unexpected(*args, **kwargs):
        pytest.fail("Blocked routing must not execute")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    result = executor.execute(_task(**updates))
    assert result.status == ResultStatus.BLOCKED
    assert result.output_text == ""
    assert result.worker == Worker.OLLAMA


def test_reference_cloud_trap_is_blocked_not_skipped(monkeypatch):
    executor = OllamaReferenceExecutor(model=MODEL)

    def unexpected(*args, **kwargs):
        pytest.fail("Detected credentials must never reach any lane")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    task = _task(
        data_class="cloud_ok",
        fallback_chain=["claude"],
        instructions="credential=" + "synthetic-fixture-value",
    )
    result = executor.execute(task)
    assert result.status == ResultStatus.BLOCKED
    assert result.output_text == ""


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
