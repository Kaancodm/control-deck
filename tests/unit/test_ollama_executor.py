"""Offline CLI adapter contracts, including refusal of unverified real execution."""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.core.schemas import ErrorCode, ResultStatus, TaskKind, Worker
from app.core.validate import validate_result
from app.db.repository import result_payload_for_storage
from app.workers.contracts import AttemptContext
from app.workers.lanes import ollama
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.process import ProcessResult

MODEL = "simulation-fixture:configured"


@pytest.fixture
def context(tmp_path):
    payload = '\ufeff{"text":"synthetic input"}\r\n'
    return AttemptContext(
        task_id=UUID("00000000-0000-4000-8000-000000000001"),
        attempt=2,
        lease_id=UUID("00000000-0000-4000-8000-000000000002"),
        lane=Worker.OLLAMA,
        kind=TaskKind.SUMMARIZE,
        model=MODEL,
        timeout_s=10,
        deadline_at=None,
        run_dir=tmp_path,
        payload_text=payload,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        redaction_applied=True,
        redaction_placeholders=2,
        cancel_requested=lambda: False,
    )


def _executor(*, behavior="ok", output="simulation-ok", delay=0, **kwargs):
    return OllamaExecutor(
        profile="simulation",
        simulation=OllamaSimulation(
            models=(MODEL,), behavior=behavior, output_text=output, delay_s=delay
        ),
        **kwargs,
    )


def _observe_processes(monkeypatch):
    calls = []
    original = ollama.run_process

    def observe(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(ollama, "run_process", observe)
    return calls


def _assert_no_output(result):
    assert result.output.text == ""
    assert result.output.files == []
    assert validate_result(result_payload_for_storage(result)) == []


def test_default_live_profile_fails_closed_without_a_process(context, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A disabled real profile must never start a subprocess")

    monkeypatch.setattr(ollama, "run_process", unexpected)
    executor = OllamaExecutor()
    availability = executor.availability((MODEL,))
    assert availability.status == "unavailable"
    assert availability.reason == "live_profile_unavailable"
    assert availability.models == ()
    result = executor.execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.error.code == ErrorCode.WORKER_UNAVAILABLE
    _assert_no_output(result)


def test_simulation_cannot_enable_a_disabled_profile(context, monkeypatch):
    calls = _observe_processes(monkeypatch)
    executor = OllamaExecutor(simulation=OllamaSimulation(models=(MODEL,)))
    assert executor.execute(context).error.code == ErrorCode.WORKER_UNAVAILABLE
    assert calls == []


def test_available_models_are_only_requested_supported_inventory():
    executor = OllamaExecutor(
        profile="simulation",
        simulation=OllamaSimulation(models=(MODEL, "simulation-other")),
    )
    available = executor.availability((MODEL, MODEL))
    assert available.status == "ok"
    assert available.reason == "simulation_ready"
    assert available.models == (MODEL,)


@pytest.mark.parametrize(
    ("models", "reason"),
    [
        ((), "model_not_configured"),
        (("missing-fixture",), "model_not_available"),
        (("simulation:cloud",), "model_unsupported"),
        (("simulation-cloud",), "model_unsupported"),
        (("../model",), "model_unsupported"),
        (("--help",), "model_unsupported"),
        (("model with space",), "model_unsupported"),
    ],
)
def test_inventory_refuses_missing_or_unsupported_models(models, reason):
    available = _executor().availability(models)
    assert available.status == "unavailable"
    assert available.reason == reason
    assert available.models == ()


def test_success_preserves_exact_output_and_metadata_without_creating_logs(context):
    output = "\ufeff  synthetic result\r\n\t"
    result = _executor(output=output).execute(context)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.output.text == output
    assert result.worker == Worker.OLLAMA
    assert result.task_id == context.task_id
    assert result.attempt == context.attempt
    assert result.payload_hash == context.payload_sha256
    assert result.redaction.applied is True
    assert result.redaction.placeholders == 2
    assert result.error is None
    assert result.log_ref == f"{context.task_id}/{context.attempt}/ollama.log"
    assert result.usage.input_tokens is None and result.usage.output_tokens is None
    assert result.timing.finished_at >= result.timing.started_at
    assert validate_result(result_payload_for_storage(result)) == []
    assert list(context.run_dir.iterdir()) == []


def test_archive_bytes_are_exact_stdin_and_never_command_arguments(context, monkeypatch):
    calls = _observe_processes(monkeypatch)
    result = _executor(behavior="echo").execute(context)
    assert result.output.text == context.payload_text
    assert len(calls) == 2
    inventory, execution = calls
    assert inventory[0][2:] == ("list",)
    assert inventory[1]["input_bytes"] == b""
    assert execution[0][0] == sys.executable
    assert Path(execution[0][1]).name == "_fake_ollama_cli.py"
    assert execution[0][2:] == ("run", MODEL)
    assert execution[1]["input_bytes"] == context.payload_text.encode("utf-8")
    assert execution[1]["cwd"] == context.run_dir
    assert all(context.payload_text not in argument for argument in execution[0])


def test_fake_processes_receive_no_inherited_credentials_profiles_or_remote_host(
    context, monkeypatch
):
    names = (
        "DECK_OWNER_TOKEN",
        "DECK_WORKER_TOKEN",
        "DECK_SECRET_KEY",
        "DECK_DATABASE_URL",
        "OLLAMA_HOST",
        "HOME",
        "PATH",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "PYTHONPATH",
        "LD_PRELOAD",
    )
    for name in names:
        monkeypatch.setenv(name, "synthetic-inherited-value")
    calls = _observe_processes(monkeypatch)
    assert _executor().execute(context).status == ResultStatus.SUCCEEDED
    for _, arguments in calls:
        assert not set(names) & arguments["env"].keys()
        assert "synthetic-inherited-value" not in arguments["env"].values()
        assert arguments["env"]["PYTHONNOUSERSITE"] == "1"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"lane": Worker.CLAUDE}, ErrorCode.INTERNAL),
        ({"payload_sha256": "0" * 64}, ErrorCode.INTERNAL),
        ({"model": None}, ErrorCode.WORKER_UNAVAILABLE),
        ({"model": "--flag"}, ErrorCode.WORKER_UNAVAILABLE),
        ({"deadline_at": datetime(2000, 1, 1, tzinfo=UTC)}, ErrorCode.TIMEOUT),
    ],
)
def test_invalid_context_is_rejected_before_spawning(context, monkeypatch, changes, code):
    calls = _observe_processes(monkeypatch)
    result = _executor().execute(replace(context, **changes))
    assert result.status == ResultStatus.FAILED
    assert result.error.code == code
    assert calls == []
    _assert_no_output(result)


def test_missing_inventory_model_never_invokes_run(context, monkeypatch):
    calls = _observe_processes(monkeypatch)
    result = _executor().execute(replace(context, model="absent-fixture"))
    assert result.error.code == ErrorCode.WORKER_UNAVAILABLE
    assert [command[2:] for command, _ in calls] == [("list",)]


@pytest.mark.parametrize(
    ("behavior", "code"),
    [
        ("fail", ErrorCode.WORKER_UNAVAILABLE),
        ("empty", ErrorCode.ACCEPTANCE_FAILED),
        ("incomplete_zero", ErrorCode.ACCEPTANCE_FAILED),
        ("invalid_utf8", ErrorCode.INTERNAL),
        ("stdout_limit", ErrorCode.INTERNAL),
        ("stderr_limit", ErrorCode.INTERNAL),
    ],
)
def test_failure_modes_are_bounded_and_never_retried(context, monkeypatch, behavior, code):
    calls = _observe_processes(monkeypatch)
    result = _executor(behavior=behavior, max_stdout_bytes=1024, max_stderr_bytes=1024).execute(
        context
    )
    assert result.status == ResultStatus.FAILED
    assert result.error.code == code
    assert result.error.message == "Ollama execution did not complete"
    assert len([command for command, _ in calls if command[2] == "run"]) == 1
    _assert_no_output(result)


def test_whitespace_only_output_is_not_accepted(context):
    result = _executor(output=" \r\n\t ").execute(context)
    assert result.error.code == ErrorCode.ACCEPTANCE_FAILED
    _assert_no_output(result)


def test_timeout_kills_the_cli_without_retry(context, monkeypatch):
    calls = _observe_processes(monkeypatch)
    started = time.monotonic()
    result = _executor(behavior="delay", delay=5, timeout_s=0.2).execute(context)
    assert time.monotonic() - started < 2
    assert result.error.code == ErrorCode.TIMEOUT
    assert len([command for command, _ in calls if command[2] == "run"]) <= 1
    _assert_no_output(result)


def test_owner_deadline_bounds_execution(context):
    context = replace(context, deadline_at=datetime.now(UTC) + timedelta(seconds=0.2))
    started = time.monotonic()
    result = _executor(behavior="delay", delay=5).execute(context)
    assert time.monotonic() - started < 2
    assert result.error.code == ErrorCode.TIMEOUT
    _assert_no_output(result)


def test_precancelled_context_never_starts_a_process(context, monkeypatch):
    calls = _observe_processes(monkeypatch)
    result = _executor().execute(replace(context, cancel_requested=lambda: True))
    assert result.status == ResultStatus.CANCELLED
    assert result.error is None
    assert calls == []
    _assert_no_output(result)


def test_cancellation_stops_an_active_cli(context):
    cancelled = threading.Event()
    timer = threading.Timer(0.2, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        result = _executor(behavior="delay", delay=5).execute(
            replace(context, cancel_requested=cancelled.is_set)
        )
    finally:
        timer.cancel()
        timer.join()
    assert time.monotonic() - started < 2
    assert result.status == ResultStatus.CANCELLED
    _assert_no_output(result)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (ProcessResult("spawn_failed", None, b"", b"", 0), "inventory_unavailable"),
        (ProcessResult("completed", 7, b"", b"private failure", 0), "inventory_unavailable"),
        (ProcessResult("completed", 0, b"unstructured private data", b"", 0), "inventory_invalid"),
        (ProcessResult("completed", 0, b"\xff", b"", 0), "inventory_invalid"),
    ],
)
def test_inventory_failures_publish_only_safe_reason_codes(monkeypatch, outcome, reason):
    monkeypatch.setattr(ollama, "run_process", lambda *args, **kwargs: outcome)
    available = _executor().availability((MODEL,))
    assert available.status == "unavailable"
    assert available.reason == reason
    assert available.models == ()


def test_cancellation_during_inventory_maps_to_cancelled(context, monkeypatch):
    monkeypatch.setattr(
        ollama, "run_process", lambda *args, **kwargs: ProcessResult("cancelled", -15, b"", b"", 0)
    )
    result = _executor().execute(context)
    assert result.status == ResultStatus.CANCELLED
    _assert_no_output(result)


def test_timeout_during_inventory_maps_to_timeout(context, monkeypatch):
    monkeypatch.setattr(
        ollama, "run_process", lambda *args, **kwargs: ProcessResult("timeout", -15, b"", b"", 0)
    )
    result = _executor().execute(context)
    assert result.error.code == ErrorCode.TIMEOUT
    _assert_no_output(result)


@pytest.mark.parametrize(
    "configuration",
    [
        {"profile": "live"},
        {"profile": "simulation"},
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"timeout_s": True},
        {"max_stdout_bytes": 0},
        {"max_stderr_bytes": 17 * 1024 * 1024},
    ],
)
def test_invalid_executor_configuration_is_rejected(configuration):
    with pytest.raises(ValueError, match="Invalid Ollama execution configuration"):
        OllamaExecutor(**configuration)


@pytest.mark.parametrize(
    "configuration",
    [
        {"models": (MODEL, MODEL)},
        {"models": ("--pull",)},
        {"models": ("fixture:cloud",)},
        {"models": (MODEL,), "behavior": "live"},
        {"models": (MODEL,), "delay_s": -1},
        {"models": (MODEL,), "delay_s": float("nan")},
        {"models": (MODEL,), "output_text": "x" * 65537},
        {"models": (MODEL,), "output_text": "null\0byte"},
    ],
)
def test_invalid_simulation_configuration_is_rejected(configuration):
    with pytest.raises(ValueError, match="Invalid Ollama simulation configuration"):
        OllamaSimulation(**configuration)


def test_simulated_output_is_omitted_from_configuration_representations():
    simulation = OllamaSimulation(models=(MODEL,), output_text="synthetic-private-output")
    assert "synthetic-private-output" not in repr(simulation)
