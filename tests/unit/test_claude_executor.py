"""Offline Claude protocol and lifecycle tests without provider credentials."""

from __future__ import annotations

import hashlib
import json
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
from app.workers import sandbox
from app.workers.contracts import AttemptContext
from app.workers.lanes.claude import ClaudeExecutor, ClaudeSimulation, parse_claude_response
from app.workers.sandbox import CleanroomSimulation

MODEL = "simulation-configured-model"


@pytest.fixture
def context(tmp_path):
    payload = '\ufeff{"text":"synthetic input"}\r\n'
    return AttemptContext(
        task_id=UUID("00000000-0000-4000-8000-000000000001"),
        attempt=1,
        lease_id=UUID("00000000-0000-4000-8000-000000000002"),
        lane=Worker.CLAUDE,
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


def executor(behavior="ok", *, output="simulation-ok", delay=0, **kwargs):
    return ClaudeExecutor(
        profile="simulation",
        simulation=ClaudeSimulation(
            models=(MODEL,),
            cleanroom=CleanroomSimulation(behavior=behavior, output_text=output, delay_s=delay),
        ),
        **kwargs,
    )


def observe(monkeypatch):
    calls = []
    original = sandbox.run_process

    def wrapped(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(sandbox, "run_process", wrapped)
    return calls


def assert_valid(result):
    assert validate_result(result_payload_for_storage(result)) == []
    assert result.output.files == []


def test_live_profile_cannot_be_activated_by_supplied_simulation(context, monkeypatch):
    calls = observe(monkeypatch)
    for adapter in (ClaudeExecutor(), ClaudeExecutor(simulation=ClaudeSimulation((MODEL,)))):
        assert adapter.availability((MODEL,)).reason == "live_profile_unavailable"
        assert adapter.execute(context).error.code == ErrorCode.WORKER_UNAVAILABLE
    assert calls == []


def test_success_preserves_result_identity_usage_and_exact_text(context):
    text = "\ufeff synthetic result\r\n\t"
    result = executor(output=text).execute(context)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.worker == Worker.CLAUDE
    assert result.task_id == context.task_id and result.attempt == context.attempt
    assert result.payload_hash == context.payload_sha256
    assert result.redaction.applied and result.redaction.placeholders == 2
    assert result.output.text == text
    assert result.usage.input_tokens == 7 and result.usage.output_tokens == 3
    assert result.log_ref.endswith("/claude.log")
    assert result.error is None
    assert result.timing.finished_at >= result.timing.started_at
    assert list(context.run_dir.iterdir()) == []
    assert_valid(result)


def test_payload_is_exact_stdin_and_every_run_has_independent_cleanup(context, monkeypatch):
    calls = observe(monkeypatch)
    result = executor("echo").execute(context)
    assert result.output.text == context.payload_text
    assert [command[2] for command, _ in calls] == ["info", "run", "rm"]
    command, arguments = calls[1]
    assert Path(command[1]).name == "_fake_cleanroom_cli.py"
    assert arguments["input_bytes"] == context.payload_text.encode("utf-8")
    assert context.payload_text not in " ".join(command)
    assert arguments["cwd"] != context.run_dir
    assert not arguments["cwd"].exists()
    assert calls[2][0][3] == "--force"
    assert calls[2][1]["input_bytes"] == b""


def test_fake_process_environment_never_inherits_host_credentials(context, monkeypatch):
    names = (
        "DECK_DATABASE_URL",
        "DECK_OWNER_TOKEN",
        "DECK_WORKER_TOKEN_CLAUDE",
        "HOME",
        "PATH",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "LD_PRELOAD",
        "PYTHONPATH",
    )
    for name in names:
        monkeypatch.setenv(name, "synthetic-inherited-value")
    calls = observe(monkeypatch)
    assert executor().execute(context).status == ResultStatus.SUCCEEDED
    for _, kwargs in calls:
        assert not set(names) & kwargs["env"].keys()
        assert "synthetic-inherited-value" not in kwargs["env"].values()


@pytest.mark.parametrize(
    ("behavior", "code"),
    [
        ("auth", ErrorCode.AUTH_REQUIRED),
        ("policy", ErrorCode.POLICY_BLOCKED),
        ("rate_limit", ErrorCode.RATE_LIMITED),
        ("unavailable", ErrorCode.WORKER_UNAVAILABLE),
        ("internal", ErrorCode.INTERNAL),
        ("error_zero", ErrorCode.INTERNAL),
        ("empty", ErrorCode.ACCEPTANCE_FAILED),
        ("invalid_json", ErrorCode.INTERNAL),
        ("invalid_utf8", ErrorCode.INTERNAL),
        ("incomplete_zero", ErrorCode.INTERNAL),
        ("permission_denied", ErrorCode.INTERNAL),
        ("negative_usage", ErrorCode.INTERNAL),
        ("boolean_usage", ErrorCode.INTERNAL),
        ("wrong_type", ErrorCode.INTERNAL),
        ("duplicate_keys", ErrorCode.INTERNAL),
        ("stdout_limit", ErrorCode.INTERNAL),
        ("stderr_limit", ErrorCode.INTERNAL),
    ],
)
def test_errors_are_bounded_classified_and_never_retried(context, monkeypatch, behavior, code):
    calls = observe(monkeypatch)
    result = executor(behavior, max_stdout_bytes=2048, max_stderr_bytes=2048).execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.error.code == code
    assert result.error.message == "Claude execution did not complete"
    assert result.output.text == ""
    assert [command[2] for command, _ in calls] == ["info", "run", "rm"]
    assert_valid(result)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"lane": Worker.OLLAMA}, ErrorCode.INTERNAL),
        ({"lane": Worker.CODEX}, ErrorCode.INTERNAL),
        ({"payload_sha256": "0" * 64}, ErrorCode.INTERNAL),
        ({"model": None}, ErrorCode.WORKER_UNAVAILABLE),
        ({"model": "--flag"}, ErrorCode.WORKER_UNAVAILABLE),
        ({"model": "missing-fixture"}, ErrorCode.WORKER_UNAVAILABLE),
        ({"deadline_at": datetime(2000, 1, 1, tzinfo=UTC)}, ErrorCode.TIMEOUT),
    ],
)
def test_invalid_context_never_reaches_container(context, monkeypatch, changes, code):
    calls = observe(monkeypatch)
    result = executor().execute(replace(context, **changes))
    assert result.error.code == code
    assert calls == []


def test_precancelled_task_starts_no_process(context, monkeypatch):
    calls = observe(monkeypatch)
    result = executor().execute(replace(context, cancel_requested=lambda: True))
    assert result.status == ResultStatus.CANCELLED
    assert calls == []


def test_timeout_stops_attempt_and_cleanup_uses_separate_budget(context, monkeypatch):
    calls = observe(monkeypatch)
    start = time.monotonic()
    result = executor("delay", delay=5, timeout_s=0.3).execute(context)
    assert time.monotonic() - start < 3
    assert result.error.code == ErrorCode.TIMEOUT
    assert calls[-1][0][2] == "rm"
    assert calls[-1][1]["timeout_s"] == 2
    assert_valid(result)


def test_cancellation_stops_cli_and_does_not_cancel_cleanup(context, monkeypatch):
    calls = observe(monkeypatch)
    cancelled = threading.Event()
    timer = threading.Timer(0.3, cancelled.set)
    timer.start()
    try:
        result = executor("delay", delay=5).execute(
            replace(context, cancel_requested=cancelled.is_set)
        )
    finally:
        timer.cancel()
        timer.join()
    assert result.status == ResultStatus.CANCELLED
    assert calls[-1][0][2] == "rm"
    assert calls[-1][1]["cancel_requested"]() is False
    assert_valid(result)


def test_owner_deadline_bounds_execution(context):
    context = replace(context, deadline_at=datetime.now(UTC) + timedelta(seconds=0.3))
    assert executor("delay", delay=5).execute(context).error.code == ErrorCode.TIMEOUT


def test_failed_cleanup_prevents_success(context):
    adapter = ClaudeExecutor(
        profile="simulation",
        simulation=ClaudeSimulation((MODEL,), CleanroomSimulation(cleanup_fails=True)),
    )
    result = adapter.execute(context)
    assert result.error.code == ErrorCode.INTERNAL
    assert result.output.text == ""


@pytest.mark.parametrize(
    ("cleanroom", "reason"),
    [
        (CleanroomSimulation(rootless=False), "rootless_required"),
        (CleanroomSimulation(cli_version="0.0.0"), "cli_version_unsupported"),
    ],
)
def test_unsupported_runtime_refuses_admission_without_spawning(
    context, monkeypatch, cleanroom, reason
):
    calls = observe(monkeypatch)
    adapter = ClaudeExecutor(profile="simulation", simulation=ClaudeSimulation((MODEL,), cleanroom))
    assert adapter.availability((MODEL,)).reason == reason
    assert adapter.execute(context).error.code == ErrorCode.WORKER_UNAVAILABLE
    assert calls == []


@pytest.mark.parametrize(
    ("models", "reason"),
    [
        ((), "model_not_configured"),
        (("missing",), "model_not_available"),
        (("../model",), "model_unsupported"),
        (("model with spaces",), "model_unsupported"),
    ],
)
def test_invalid_model_admission(models, reason):
    assert executor().availability(models).reason == reason


def test_availability_deduplicates_only_requested_models():
    available = executor().availability((MODEL, MODEL))
    assert available.models == (MODEL,)
    assert available.status == "ok" and available.reason == "simulation_ready"


@pytest.mark.parametrize(
    "configuration",
    [
        {"profile": "live"},
        {"profile": "simulation"},
        {"timeout_s": 0},
        {"timeout_s": True},
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
        {"max_stdout_bytes": 0},
        {"max_stderr_bytes": 17 * 1024 * 1024},
    ],
)
def test_invalid_executor_configuration(configuration):
    with pytest.raises(ValueError, match="Invalid Claude execution configuration"):
        ClaudeExecutor(**configuration)


@pytest.mark.parametrize("models", [(MODEL, MODEL), ("--flag",), [MODEL], ("has space",)])
def test_invalid_simulated_model_configuration(models):
    with pytest.raises(ValueError, match="Invalid Claude simulation"):
        ClaudeSimulation(models)


def test_private_fixture_output_is_not_in_representations():
    config = ClaudeSimulation((MODEL,), CleanroomSimulation(output_text="synthetic-private-result"))
    assert "synthetic-private-result" not in repr(config)
    assert "synthetic-private-result" not in repr(
        parse_claude_response(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "terminal_reason": "completed",
                    "num_turns": 1,
                    "result": "synthetic-private-result",
                }
            ).encode(),
            0,
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        {"type": "assistant"},
        {"terminal_reason": "aborted_streaming"},
        {"terminal_reason": "aborted_tools"},
        {"num_turns": 2},
        {"num_turns": True},
        {"usage": {"output_tokens": None}},
        {"usage": {"output_tokens": 2**53}},
        {"result": {"text": "not a string"}},
        {"tool_use": []},
    ],
)
def test_protocol_rejects_incomplete_tool_or_invalid_usage_results(change):
    message = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "terminal_reason": "completed",
        "num_turns": 1,
        "result": "synthetic",
    }
    message.update(change)
    assert parse_claude_response(json.dumps(message).encode(), 0).error == ErrorCode.INTERNAL


def test_zero_exit_does_not_hide_structured_rate_limit():
    response = parse_claude_response(
        b'{"type":"result","subtype":"success","is_error":true,"api_error_status":429}', 0
    )
    assert response.error == ErrorCode.RATE_LIMITED


def test_nonzero_exit_does_not_accept_a_success_object():
    response = parse_claude_response(
        b'{"type":"result","subtype":"success","is_error":false,"terminal_reason":"completed","num_turns":1,"result":"synthetic"}',
        1,
    )
    assert response.error == ErrorCode.INTERNAL
