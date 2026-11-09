"""A queue-full 503 is retried within bounds, then reported as a typed busy failure."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from app.assistant import async_turns
from app.assistant.errors import MODEL_BUSY_CODE, MODEL_BUSY_TEXT, AssistantError
from app.assistant.ollama_chat import OllamaDialogueAdapter
from app.core.schemas import ErrorCode, ResultStatus
from app.ui.views import ERROR_MESSAGES
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.lanes.ollama_http import (
    BUSY_ATTEMPTS,
    OllamaHTTPClient,
    OllamaHTTPError,
    busy_delay_s,
)
from app.workers.provenance import safe_execution_metadata
from tests.support.ollama_stub import OllamaStub
from tests.unit.test_ollama_executor import MODEL
from tests.unit.test_ollama_executor import context as context

CHAT = "/api/chat"
ADAPTER_MODEL = "fixture:localtest"


def fast_client(stub, **kwargs):
    return OllamaHTTPClient(stub.url, busy_backoff_s=0.01, busy_backoff_max_s=0.02, **kwargs)


def call_chat(client, **kwargs):
    return client.chat(
        model=MODEL,
        payload_text="synthetic payload",
        timeout_s=kwargs.pop("timeout_s", 5),
        cancelled=kwargs.pop("cancelled", lambda: False),
    )


def call_streaming(client, chunks, **kwargs):
    return client.chat_with_chunks(
        model=MODEL,
        messages=[{"role": "user", "content": "synthetic payload"}],
        timeout_s=kwargs.pop("timeout_s", 5),
        cancelled=kwargs.pop("cancelled", lambda: False),
        on_chunk=chunks.append,
    )


def chat_requests(stub):
    return [request for request in stub.requests if request["path"] == CHAT]


def adapter_for(stub):
    return OllamaDialogueAdapter(url=stub.url, model=ADAPTER_MODEL, client=fast_client(stub))


@pytest.mark.parametrize(
    ("attempt", "retry_after", "expected"),
    [
        (1, None, 0.5),
        (2, None, 1.0),
        (3, None, 2.0),
        (8, None, 4.0),
        (1, 0.0, 0.0),
        (1, 3.0, 3.0),
        (1, 3600.0, 4.0),
    ],
)
def test_delay_honors_retry_after_and_stays_under_the_cap(attempt, retry_after, expected):
    assert busy_delay_s(attempt, retry_after) == expected


def test_sync_chat_recovers_after_503():
    with OllamaStub(output="recovered", status_sequence_by_path={CHAT: [503, 503, 200]}) as stub:
        response = call_chat(fast_client(stub))
    assert response.content == "recovered"
    assert len(chat_requests(stub)) == 3


def test_streaming_chat_recovers_after_503_without_duplicate_chunks():
    chunks: list[str] = []
    with OllamaStub(output="recovered", status_sequence_by_path={CHAT: [503, 200]}) as stub:
        response = call_streaming(fast_client(stub), chunks)
    assert response.content == "recovered"
    assert chunks == ["recovered"]
    assert len(chat_requests(stub)) == 2


@pytest.mark.parametrize("streaming", [False, True])
def test_exhausted_503_is_a_typed_busy_failure(streaming):
    chunks: list[str] = []
    with OllamaStub(status_by_path={CHAT: 503}) as stub:
        client = fast_client(stub)
        with pytest.raises(OllamaHTTPError) as caught:
            if streaming:
                call_streaming(client, chunks)
            else:
                call_chat(client)
    assert caught.value.reason == "busy"
    assert caught.value.status_code == 503 and caught.value.phase == "status"
    assert "provider error marker" not in str(caught.value)
    assert chunks == []
    assert len(chat_requests(stub)) == BUSY_ATTEMPTS


def test_retry_after_zero_retries_without_the_backoff():
    with OllamaStub(
        output="recovered", retry_after="0", status_sequence_by_path={CHAT: [503, 200]}
    ) as stub:
        started = time.monotonic()
        response = call_chat(OllamaHTTPClient(stub.url))
    assert response.content == "recovered"
    assert time.monotonic() - started < 0.4


def test_large_retry_after_cannot_outlast_the_caller_budget():
    with OllamaStub(retry_after="3600", status_by_path={CHAT: 503}) as stub:
        started = time.monotonic()
        with pytest.raises(OllamaHTTPError) as caught:
            call_chat(OllamaHTTPClient(stub.url), timeout_s=1)
    assert caught.value.reason == "busy"
    assert time.monotonic() - started < 0.9
    assert len(chat_requests(stub)) == 1


@pytest.mark.parametrize("header", ["soon", "Wed, 21 Oct 2026 07:28:00 GMT", "-1", "9" * 5000])
def test_unusable_retry_after_falls_back_to_the_backoff(header):
    with OllamaStub(
        output="recovered", retry_after=header, status_sequence_by_path={CHAT: [503, 200]}
    ) as stub:
        assert call_chat(fast_client(stub)).content == "recovered"


def test_cancel_during_backoff_stops_retrying():
    cancelled = threading.Event()
    with OllamaStub(status_by_path={CHAT: 503}) as stub:
        timer = threading.Timer(0.05, cancelled.set)
        timer.start()
        started = time.monotonic()
        with pytest.raises(OllamaHTTPError) as caught:
            call_chat(OllamaHTTPClient(stub.url), cancelled=cancelled.is_set)
        timer.join()
    assert caught.value.reason == "cancelled"
    assert time.monotonic() - started < 0.4
    assert len(chat_requests(stub)) == 1


@pytest.mark.parametrize("status", [500, 502, 504])
def test_other_server_errors_are_not_retried(status):
    with OllamaStub(status_by_path={CHAT: status}) as stub:
        with pytest.raises(OllamaHTTPError) as caught:
            call_chat(fast_client(stub))
    assert caught.value.reason == "unavailable" and caught.value.status_code == status
    assert len(chat_requests(stub)) == 1


@pytest.mark.parametrize("options", [{"busy_attempts": 0}, {"busy_backoff_s": 0}])
def test_invalid_retry_settings_are_rejected(options):
    with pytest.raises(ValueError):
        OllamaHTTPClient("http://127.0.0.1:11434", **options)


@pytest.mark.parametrize("streaming", [False, True])
def test_dialogue_recovers_after_503(streaming):
    chunks: list[str] = []
    with OllamaStub(output="recovered", status_sequence_by_path={CHAT: [503, 200]}) as stub:
        reply = adapter_for(stub).complete_messages(
            [{"role": "user", "content": "Hallo"}],
            data_class="local_only",
            on_chunk=chunks.append if streaming else None,
        )
    assert reply.text == "recovered"
    assert reply.loopback_calls == 1 and reply.model_identity == "unverified"
    assert chunks == (["recovered"] if streaming else [])
    assert len(chat_requests(stub)) == 2


@pytest.mark.parametrize("streaming", [False, True])
def test_dialogue_reports_busy_without_the_error_body(streaming):
    chunks: list[str] = []
    with OllamaStub(status_by_path={CHAT: 503}) as stub:
        with pytest.raises(AssistantError) as busy:
            adapter_for(stub).complete_messages(
                [{"role": "user", "content": "Hallo"}],
                data_class="local_only",
                on_chunk=chunks.append if streaming else None,
            )
    assert busy.value.code == MODEL_BUSY_CODE
    assert busy.value.detail["class"] == "busy" and busy.value.detail["reason"] == "busy"
    assert busy.value.detail["http_status"] == 503 and busy.value.detail["phase"] == "status"
    assert "provider error marker" not in str(busy.value) + json.dumps(busy.value.detail)
    assert chunks == []


def test_busy_turn_gives_the_stream_a_readable_message(monkeypatch):
    class Session:
        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssistantError(MODEL_BUSY_CODE)

    with async_turns._cond:
        async_turns._turns.clear()
    monkeypatch.setattr(async_turns, "_ensure_worker", lambda: None)
    with patch("app.assistant.async_turns.reply_to_owner", side_effect=refuse):
        turn_id = async_turns.start_dialogue_turn(
            Session,
            message="Hallo",
            data_class="local_only",
            profile_notes=None,
            history=(),
            persist=False,
        )
        async_turns._run_turn(async_turns._work_queue.get())
    frames = "".join(async_turns.stream_sse_events(turn_id, poll_timeout_s=2.0))
    assert "event: failed" in frames
    assert "Ollama ausgelastet." in frames and MODEL_BUSY_CODE in frames
    assert ERROR_MESSAGES[MODEL_BUSY_CODE] == MODEL_BUSY_TEXT


def live_executor(stub):
    executor = OllamaExecutor(profile="live", url=stub.url)
    executor.client = fast_client(stub)
    return executor


def test_lane_recovers_after_503(context):
    with OllamaStub(
        models=(MODEL,), output="recovered", status_sequence_by_path={CHAT: [503, 503, 200]}
    ) as stub:
        result = live_executor(stub).execute(context)
    assert result.status == ResultStatus.SUCCEEDED and result.output.text == "recovered"
    assert len(chat_requests(stub)) == 3


def test_lane_reports_exhausted_503_as_rate_limited(context):
    with OllamaStub(models=(MODEL,), status_by_path={CHAT: 503}) as stub:
        executor = live_executor(stub)
        result = executor.execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.error.code == ErrorCode.RATE_LIMITED
    assert result.output.text == ""
    assert safe_execution_metadata(executor.last_metadata)["http_status"] == 503
    assert len(chat_requests(stub)) == BUSY_ATTEMPTS


def test_lane_preflight_busy_is_rate_limited_and_never_chats(context):
    with OllamaStub(models=(MODEL,), status_by_path={"/api/version": 503}) as stub:
        executor = live_executor(stub)
        result = executor.execute(context)
    assert result.status == ResultStatus.FAILED
    assert result.error.code == ErrorCode.RATE_LIMITED
    assert chat_requests(stub) == []


def test_lane_availability_names_the_busy_reason():
    with OllamaStub(models=(MODEL,), status_by_path={"/api/tags": 503}) as stub:
        available = live_executor(stub).availability((MODEL,))
    assert available.status == "unavailable" and available.reason == "inventory_busy"
