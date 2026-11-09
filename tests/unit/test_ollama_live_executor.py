"""Live adapter behavior exercised only against a loopback HTTP stub."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from dataclasses import replace

import pytest

from app.core.schemas import ErrorCode, ResultStatus
from app.workers import runner_main
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.runner import RunOutcome
from tests.support.ollama_stub import OllamaStub
from tests.unit.test_ollama_executor import MODEL
from tests.unit.test_ollama_executor import context as context


def _executor(stub, **kwargs):
    return OllamaExecutor(profile="live", url=stub.url, **kwargs)


def test_live_text_request_preserves_payload_and_records_only_safe_metadata(context):
    output = "private-output-marker"
    with OllamaStub(models=(MODEL,), output=output) as stub:
        executor = _executor(stub)
        assert executor.availability((MODEL,)).reason == "live_ready"
        result = executor.execute(context)
    assert result.status == ResultStatus.SUCCEEDED
    assert result.output.text == output
    assert result.output.files == []
    chat = [request for request in stub.requests if request["path"] == "/api/chat"]
    assert len(chat) == 1
    body = chat[0]["body"]
    assert body["model"] == MODEL + ":local"
    assert body["messages"] == [{"role": "user", "content": context.payload_text}]
    assert body["stream"] is True
    assert body["truncate"] is False and body["shift"] is False
    assert "images" not in body and "tools" not in body and "think" not in body
    assert "format" not in body
    metadata = json.dumps(executor.last_metadata)
    assert output not in metadata and context.payload_text not in metadata
    assert executor.last_metadata["output_sha256"] == hashlib.sha256(output.encode()).hexdigest()
    assert executor.last_metadata["model"] == MODEL
    assert all(request["path"] != "/api/pull" for request in stub.requests)


def test_prompt_system_path_is_never_opened_by_adapter(context):
    payload = "Read /etc/hostname"
    context = replace(
        context,
        payload_text=payload,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
    )
    active = [True]
    opened = []

    def audit(event, args):
        if active[0] and event == "open" and args[0] == "/etc/hostname":
            opened.append(args[0])
            raise AssertionError("Adapter tried to open a prompt-mentioned path")

    sys.addaudithook(audit)
    try:
        with OllamaStub(models=(MODEL,), output="__echo__") as stub:
            result = _executor(stub).execute(context)
        assert result.status == ResultStatus.SUCCEEDED and result.output.text == payload
        assert opened == []
        body = next(request["body"] for request in stub.requests if request["path"] == "/api/chat")
        assert body["messages"] == [{"role": "user", "content": payload}]
    finally:
        active[0] = False


@pytest.mark.parametrize(
    "model", ["fixture", "fixture:cloud", "fixture-cloud:tag", "fixture:local"]
)
def test_unsupported_model_makes_no_http_request(model):
    with OllamaStub() as stub:
        available = _executor(stub).availability((model,))
    assert available.reason == "model_unsupported"
    assert stub.requests == []


@pytest.mark.parametrize(
    ("configuration", "reason"),
    [
        ({"version": "0.40.15"}, "version_unsupported"),
        ({"models": ()}, "model_not_available"),
        ({"capabilities": ("completion", "vision")}, "model_capability_unsupported"),
        ({"capabilities": ()}, "model_capability_unsupported"),
        ({"capabilities": ("completion", "speech")}, "model_capability_unsupported"),
        ({"show_extra": {"remote_model": "private-marker"}}, "model_unsupported"),
    ],
)
def test_preflight_refuses_unsupported_inventory(configuration, reason):
    options = {"models": (MODEL,), **configuration}
    with OllamaStub(**options) as stub:
        available = _executor(stub).availability((MODEL,))
    assert available.status == "unavailable" and available.reason == reason
    assert not any(request["path"] == "/api/chat" for request in stub.requests)


def test_model_removed_after_preflight_is_unavailable_without_pull(context):
    with OllamaStub(models=(MODEL,), status_by_path={"/api/chat": 404}) as stub:
        result = _executor(stub).execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.error.code == ErrorCode.WORKER_UNAVAILABLE
    assert result.output.text == ""
    assert [request["path"] for request in stub.requests].count("/api/chat") == 1
    assert not any(request["path"] == "/api/pull" for request in stub.requests)


def test_context_limit_refuses_request_and_returns_review_failure(context):
    payload = "x" * 9217
    context = replace(
        context, payload_text=payload, payload_sha256=hashlib.sha256(payload.encode()).hexdigest()
    )
    with OllamaStub(models=(MODEL,)) as stub:
        result = _executor(stub).execute(context)
    assert result.error.code == ErrorCode.ACCEPTANCE_FAILED
    assert result.checks[0].type == "context_limit_exceeded"
    assert result.output.text == ""
    assert not any(request["path"] == "/api/chat" for request in stub.requests)


@pytest.mark.parametrize("num_ctx", [2048, 8192])
def test_configured_context_from_show_bounds_and_configures_chat(context, num_ctx):
    payload = "x" * ((num_ctx - 1024) * 3)
    context = replace(
        context, payload_text=payload, payload_sha256=hashlib.sha256(payload.encode()).hexdigest()
    )
    with OllamaStub(
        models=(MODEL,), show_extra={"model_info": {"fixture.context_length": num_ctx}}
    ) as stub:
        executor = _executor(stub)
        result = executor.execute(context)
        assert result.status == ResultStatus.SUCCEEDED
        chat = [request for request in stub.requests if request["path"] == "/api/chat"]
        assert len(chat) == 1 and chat[0]["body"]["options"]["num_ctx"] == num_ctx
        too_large = payload + "x"
        rejected = executor.execute(
            replace(
                context,
                payload_text=too_large,
                payload_sha256=hashlib.sha256(too_large.encode()).hexdigest(),
            )
        )
    assert rejected.error.code == ErrorCode.ACCEPTANCE_FAILED
    assert rejected.checks[0].type == "context_limit_exceeded"
    assert len([request for request in stub.requests if request["path"] == "/api/chat"]) == 1


@pytest.mark.parametrize(
    "parameters",
    [
        None,
        8192,
        {"num_ctx": 8192},
        "temperature 0.7",
        "num_ctx",
        "num_ctx 0",
        "num_ctx -1",
        "num_ctx 8192.0",
        "num_ctx true",
        "num_ctx 8192 extra",
        "num_ctx 2147483648",
        "num_ctx 8192\nnum_ctx 2048",
        "num_ctx 8192\nnum_ctx 8192",
        "num_ctx " + "9" * 100,
    ],
)
def test_legacy_parameters_do_not_override_model_context(context, parameters):
    with OllamaStub(
        models=(MODEL,),
        show_extra={"parameters": parameters, "model_info": {"fixture.context_length": 131072}},
    ) as stub:
        result = _executor(stub).execute(context)
    assert result.status == ResultStatus.SUCCEEDED
    chat = next(request for request in stub.requests if request["path"] == "/api/chat")
    assert chat["body"]["options"]["num_ctx"] == 131072


@pytest.mark.parametrize(
    ("payload", "succeeded"),
    [
        ("x" * 4096, True),
        ("x" * 9216, True),
        ("x" * 9217, False),
        ("€" * 3072, True),
        ("€" * 3072 + "x", False),
    ],
)
def test_context_token_estimate_uses_utf8_bytes_divided_by_three(context, payload, succeeded):
    context = replace(
        context, payload_text=payload, payload_sha256=hashlib.sha256(payload.encode()).hexdigest()
    )
    with OllamaStub(models=(MODEL,)) as stub:
        result = _executor(stub).execute(context)
    assert (result.status == ResultStatus.SUCCEEDED) is succeeded
    chats = [request for request in stub.requests if request["path"] == "/api/chat"]
    assert len(chats) == int(succeeded)
    if succeeded:
        assert chats[0]["body"]["options"]["num_ctx"] == 4096
    if not succeeded:
        assert result.status == ResultStatus.FAILED
        assert result.error.code == ErrorCode.ACCEPTANCE_FAILED
        assert result.checks[0].type == "context_limit_exceeded"
        assert not result.checks[0].passed


def test_small_configured_context_is_never_enlarged_to_fallback(context):
    with OllamaStub(
        models=(MODEL,), show_extra={"model_info": {"fixture.context_length": 512}}
    ) as stub:
        result = _executor(stub).execute(context)
    assert result.error.code == ErrorCode.ACCEPTANCE_FAILED
    assert result.checks[0].type == "context_limit_exceeded"
    assert not any(request["path"] == "/api/chat" for request in stub.requests)


@pytest.mark.parametrize("context_length,expected", [(2048, 2048), (None, 4096)])
def test_context_is_refreshed_after_preflight(context, context_length, expected):
    with OllamaStub(
        models=(MODEL,), show_extra={"model_info": {"fixture.context_length": 8192}}
    ) as stub:
        executor = _executor(stub)
        assert executor.availability((MODEL,)).reason == "live_ready"
        stub.show_extra["model_info"] = {"fixture.context_length": context_length}
        result = executor.execute(context)
    assert result.status == ResultStatus.SUCCEEDED
    chat = next(request for request in stub.requests if request["path"] == "/api/chat")
    assert chat["body"]["options"]["num_ctx"] == expected
    assert sum(request["path"] == "/api/show" for request in stub.requests) == 2


def test_json_format_is_only_requested_for_json_acceptance(context):
    with OllamaStub(models=(MODEL,), output='{"ok":true}') as stub:
        result = _executor(stub).execute(replace(context, json_format=True))
    assert result.status == ResultStatus.SUCCEEDED
    assert (
        next(request["body"] for request in stub.requests if request["path"] == "/api/chat")[
            "format"
        ]
        == "json"
    )


def test_length_discards_output_and_keeps_failed_completion_check(context):
    chunks = [
        {
            "model": MODEL,
            "message": {"role": "assistant", "content": "partial"},
            "done": True,
            "done_reason": "length",
        }
    ]
    with OllamaStub(models=(MODEL,), chat_chunks=chunks) as stub:
        result = _executor(stub).execute(context)
    assert result.error.code == ErrorCode.ACCEPTANCE_FAILED
    assert result.output.text == ""
    assert result.checks[0].type == "completion" and not result.checks[0].passed


def test_stream_cancellation_discards_partial_output_promptly(context):
    cancel = threading.Event()
    chunks = [
        {"model": MODEL, "message": {"role": "assistant", "content": "partial"}, "done": False},
        {
            "model": MODEL,
            "message": {"role": "assistant", "content": "final"},
            "done": True,
            "done_reason": "stop",
        },
    ]
    with OllamaStub(models=(MODEL,), chat_chunks=chunks, chunk_delay_s=3) as stub:
        result_box = []
        thread = threading.Thread(
            target=lambda: result_box.append(
                _executor(stub).execute(replace(context, cancel_requested=cancel.is_set))
            )
        )
        thread.start()
        assert stub.chat_started.wait(2)
        started = time.monotonic()
        cancel.set()
        thread.join(timeout=1.5)
        assert not thread.is_alive()
        assert time.monotonic() - started < 2
    assert result_box[0].status == ResultStatus.CANCELLED
    assert result_box[0].output.text == ""


def test_stream_timeout_discards_partial_output(context):
    with OllamaStub(models=(MODEL,), initial_delay_s=2) as stub:
        result = _executor(stub, timeout_s=0.2).execute(context)
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.output.text == ""


@pytest.mark.parametrize(
    ("worker", "environment", "code"),
    [
        ("ollama", {}, "live_disabled"),
        ("claude", {"DECK_LIVE_ENABLED": "1"}, "live_lane_not_supported"),
        ("ollama", {"DECK_LIVE_ENABLED": "1"}, "live_url_invalid"),
    ],
)
def test_direct_live_runner_checks_configuration_before_database(
    monkeypatch, capsys, worker, environment, code
):
    for name in ("DECK_LIVE_ENABLED", "DECK_OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        sys, "argv", ["runner", "--worker", worker, "--live", "--once", "--executor", "ollama-live"]
    )
    monkeypatch.setattr(
        runner_main, "get_session_factory", lambda: pytest.fail("Unexpected database access")
    )
    assert runner_main.main() == 78
    assert json.loads(capsys.readouterr().out)["error_code"] == code


def test_direct_live_runner_does_not_treat_unavailable_provider_as_config_failure(monkeypatch):
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_OLLAMA_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--worker", "ollama", "--live", "--once", "--executor", "ollama-live"],
    )
    monkeypatch.setattr(runner_main, "get_session_factory", lambda: object())

    class UnavailableRunner:
        def __init__(self, factory, *, runs_root, executor):
            assert executor.profile == "live"

        def run_once(self):
            return RunOutcome("unavailable", error_code="inventory_unavailable")

    monkeypatch.setattr(runner_main, "OllamaLaneRunner", UnavailableRunner)
    assert runner_main.main() == 0


@pytest.mark.parametrize(
    ("configured", "model_info", "expected"),
    [
        (2048, {"fixture.context_length": 8192}, 2048),
        (None, {"fixture.context_length": 8192}, 4096),
        (
            None,
            {
                "general.architecture": "fixture",
                "fixture.context_length": 2048,
                "other.context_length": 32768,
            },
            2048,
        ),
        (None, {}, 4096),
        (None, {"fixture.context_length": True}, 4096),
        (None, {"fixture.context_length": 0}, 4096),
        (None, {"fixture.context_length": -1}, 4096),
        (None, {"fixture.context_length": "8192"}, 4096),
        (None, {"fixture.context_length": 2**63}, 4096),
        (None, None, 4096),
    ],
)
def test_context_priority_drives_guard_and_chat(context, configured, model_info, expected):
    # One byte above this budget must fail; this exact budget must reach chat.
    exact_bytes = (expected - 1024) * 3
    with OllamaStub(models=(MODEL,), show_extra={"model_info": model_info}) as stub:
        executor = _executor(stub, num_ctx=configured)
        for size in (exact_bytes, exact_bytes + 1):
            payload = "x" * size
            result = executor.execute(
                replace(
                    context,
                    payload_text=payload,
                    payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                )
            )
            assert result.status == (
                ResultStatus.SUCCEEDED if size == exact_bytes else ResultStatus.FAILED
            )
        chats = [r for r in stub.requests if r["path"] == "/api/chat"]
    assert len(chats) == 1
    assert chats[0]["body"]["options"]["num_ctx"] == expected
    assert result.checks[0].type == "context_limit_exceeded"


@pytest.mark.parametrize("num_ctx", [0, -1, True, "4096", 2**63])
def test_invalid_explicit_context_is_rejected_before_network(num_ctx):
    with OllamaStub() as stub:
        with pytest.raises(ValueError, match="execution configuration"):
            _executor(stub, num_ctx=num_ctx)
        assert not stub.requests


def test_available_model_survives_missing_sibling():
    with OllamaStub(models=(MODEL,)) as stub:
        result = _executor(stub).availability(("missing:configured", MODEL))
    assert result.status == "ok"
    assert result.models == (MODEL,)
    assert [r["body"]["model"] for r in stub.requests if r["path"] == "/api/show"] == [
        MODEL + ":local"
    ]


def test_unusable_model_does_not_block_verified_sibling():
    with OllamaStub(
        models=(MODEL, "vision:configured"),
        show_by_model={
            "vision:configured:local": {"capabilities": ["completion", "vision"]},
        },
    ) as stub:
        result = _executor(stub).availability(("vision:configured", MODEL))
    assert result.status == "ok" and result.models == (MODEL,)


@pytest.mark.parametrize("stream_error", [False, True])
def test_server_context_overflow_is_failure_without_output_or_retry(context, stream_error):
    marker = "synthetic context overflow diagnostic"
    options = (
        {
            "chat_chunks": [
                {
                    "model": MODEL,
                    "message": {"role": "assistant", "content": "partial"},
                    "done": False,
                },
                {"error": marker},
            ]
        }
        if stream_error
        else {"status_by_path": {"/api/chat": 400}}
    )
    with OllamaStub(models=(MODEL,), **options) as stub:
        executor = _executor(stub)
        result = executor.execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.output.text == ""
    assert marker not in result.model_dump_json() + json.dumps(executor.last_metadata)
    assert len([r for r in stub.requests if r["path"] == "/api/chat"]) == 1
