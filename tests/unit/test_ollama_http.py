"""Exercise HTTP bounds, cancellation, and closed text protocol against local sockets."""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

from app.workers.lanes.ollama_http import OllamaHTTPClient, OllamaHTTPError
from tests.support.ollama_stub import OllamaStub

MODEL = "fixture:latest"


def chunk(content="", *, done=False, **extra):
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": content},
        "done": done,
        **({"done_reason": "stop"} if done else {}),
        **extra,
    }


def chat(client, **kwargs):
    return client.chat(
        model=MODEL,
        payload_text="synthetic payload",
        timeout_s=kwargs.pop("timeout_s", 2),
        cancelled=kwargs.pop("cancelled", lambda: False),
        **kwargs,
    )


def test_metadata_and_exact_text_request_ignore_proxy_environment(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    payload = '\ufeffline\r\n{"text":"/etc/hostname"}\n'
    with OllamaStub(output="__echo__") as stub:
        client = OllamaHTTPClient(stub.url)
        assert client.version() == {"version": "0.40.14"}
        assert client.tags() == {"models": [{"name": MODEL}]}
        assert client.show(MODEL)["capabilities"] == ["completion"]
        response = client.chat(
            model=MODEL,
            payload_text=payload,
            timeout_s=2,
            cancelled=lambda: False,
            format_json=True,
        )
        assert response.content.encode() == payload.encode()
        assert response.output_sha256 == hashlib.sha256(payload.encode()).hexdigest()
        assert response.prompt_eval_count == 3 and response.eval_count == 4
        assert response.done_reason == "stop"
        request = stub.requests[-1]
        assert request["body"] == {
            "model": MODEL,
            "messages": [{"role": "user", "content": payload}],
            "stream": True,
            "truncate": False,
            "shift": False,
            "options": {"temperature": 0, "num_ctx": 4096},
            "keep_alive": "1m",
            "format": "json",
        }
        assert all(request["headers"]["Connection"] == "close" for request in stub.requests)
        assert [request["path"] for request in stub.requests] == [
            "/api/version",
            "/api/tags",
            "/api/show",
            "/api/chat",
        ]


@pytest.mark.parametrize("status", [301, 302, 307, 404, 500])
def test_status_errors_have_no_body_no_redirect_and_no_pull(status):
    with OllamaStub(status_by_path={"/api/chat": status}) as stub:
        with pytest.raises(OllamaHTTPError) as caught:
            chat(OllamaHTTPClient(stub.url))
        assert caught.value.reason == "unavailable"
        assert caught.value.status_code == status
        assert "provider error marker" not in str(caught.value)
        assert len(stub.requests) == 1
        assert stub.requests[0]["path"] == "/api/chat"


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff"])
def test_invalid_metadata_is_sanitized(raw):
    with OllamaStub(raw_by_path={"/api/tags": raw}) as stub:
        with pytest.raises(OllamaHTTPError, match="internal"):
            OllamaHTTPClient(stub.url).tags()


def test_metadata_size_bound():
    with OllamaStub(raw_by_path={"/api/tags": b" " * 1025}) as stub:
        with pytest.raises(OllamaHTTPError, match="internal"):
            OllamaHTTPClient(stub.url, max_metadata_bytes=1024).tags()


@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [chunk("partial")],
        [chunk("partial"), b"not JSON\n"],
        [chunk("partial"), chunk(done=True, done_reason="unknown")],
        [chunk("partial"), chunk(done=True), chunk("late")],
        [chunk("partial"), b'{"done":true,"done":false}\n'],
        [chunk("partial"), chunk(done=True, eval_count=True)],
        [chunk("partial"), chunk(done=True, prompt_eval_count=-1)],
        [chunk("partial"), chunk(done=True, model="different:latest")],
        [chunk("partial"), chunk(done=True, remote_model="remote-marker")],
        [chunk("partial"), chunk(done=True, error="provider-marker")],
        [chunk("partial"), chunk(done=True, message={"role": "assistant", "content": 3})],
        [
            chunk("partial"),
            chunk(done=True, message={"role": "assistant", "content": "", "tool_calls": [{}]}),
        ],
        [json.dumps(chunk(done=True)).encode()],
    ],
)
def test_incomplete_or_malformed_stream_discards_partial_output(chunks):
    with OllamaStub(chat_chunks=chunks) as stub:
        with pytest.raises(OllamaHTTPError) as caught:
            chat(OllamaHTTPClient(stub.url))
        assert caught.value.reason == "internal"
        assert "partial" not in repr(caught.value)
        assert not hasattr(caught.value, "content")


def test_length_is_distinct_and_thinking_is_discarded():
    thought = chunk("answer")
    thought["message"]["thinking"] = "private reasoning marker"
    with OllamaStub(chat_chunks=[thought, chunk(done=True, done_reason="length")]) as stub:
        response = chat(OllamaHTTPClient(stub.url))
        assert response.content == "answer"
        assert response.done_reason == "length"
        assert "private reasoning marker" not in repr(response)


@pytest.mark.parametrize(
    ("chunks", "limits"),
    [
        ([b"x" * 129], {"max_line_bytes": 128}),
        ([chunk("x" * 129), chunk(done=True)], {"max_output_bytes": 128}),
        ([chunk("x") for _ in range(20)] + [chunk(done=True)], {"max_stream_bytes": 512}),
    ],
)
def test_line_output_and_total_stream_bounds(chunks, limits):
    with OllamaStub(chat_chunks=chunks) as stub:
        with pytest.raises(OllamaHTTPError, match="internal"):
            chat(OllamaHTTPClient(stub.url, **limits))


def test_ignored_thinking_still_counts_against_stream_limit():
    thought = chunk()
    thought["message"]["thinking"] = "x" * 200
    with OllamaStub(chat_chunks=[thought] * 20 + [chunk(done=True)]) as stub:
        with pytest.raises(OllamaHTTPError, match="internal"):
            chat(OllamaHTTPClient(stub.url, max_stream_bytes=1024))


@pytest.mark.parametrize("delay_setting", ["header_delay_s", "initial_delay_s", "chunk_delay_s"])
def test_cancellation_interrupts_headers_first_chunk_and_between_chunks(delay_setting):
    cancelled = threading.Event()
    with OllamaStub(**{delay_setting: 5}) as stub:
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            with pytest.raises(OllamaHTTPError) as caught:
                chat(OllamaHTTPClient(stub.url), cancelled=cancelled.is_set)
        finally:
            timer.cancel()
        assert caught.value.reason == "cancelled"
        assert time.monotonic() - started < 2
        assert not any(thread.name == "deck-ollama-http" for thread in threading.enumerate())


@pytest.mark.parametrize("delay_setting", ["header_delay_s", "initial_delay_s", "chunk_delay_s"])
def test_total_timeout_interrupts_stalled_socket(delay_setting):
    with OllamaStub(**{delay_setting: 5}) as stub:
        started = time.monotonic()
        with pytest.raises(OllamaHTTPError) as caught:
            chat(OllamaHTTPClient(stub.url), timeout_s=0.1)
        assert caught.value.reason == "timeout"
        assert time.monotonic() - started < 2


@pytest.mark.parametrize("delay_setting", ["initial_delay_s", "chunk_delay_s"])
def test_first_chunk_and_following_idle_budgets(delay_setting):
    with OllamaStub(**{delay_setting: 5}) as stub:
        with pytest.raises(OllamaHTTPError) as caught:
            chat(OllamaHTTPClient(stub.url, first_chunk_timeout_s=0.1, idle_timeout_s=0.1))
        assert caught.value.reason == "timeout"


def test_cancelled_before_connect_has_no_request():
    with OllamaStub() as stub:
        with pytest.raises(OllamaHTTPError, match="cancelled"):
            chat(OllamaHTTPClient(stub.url), cancelled=lambda: True)
        assert stub.requests == []


def test_local_suffix_response_uses_configured_name():
    with OllamaStub(chat_chunks=[chunk("answer"), chunk(done=True)]) as stub:
        response = OllamaHTTPClient(stub.url).chat(
            model=f"{MODEL}:local", payload_text="payload", timeout_s=2, cancelled=lambda: False
        )
        assert response.content == "answer"
