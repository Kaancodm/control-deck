"""Bounded text-only Ollama HTTP transport; all connections use literal loopback."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar, cast

from app.workers.loopback_url import validate_ollama_url

FailureReason = Literal["cancelled", "timeout", "unavailable", "internal", "busy"]
T = TypeVar("T")

# Ollama answers 503 at once when OLLAMA_MAX_QUEUE is full, before any inference starts.
_BUSY_STATUS = 503
BUSY_ATTEMPTS = 4
BUSY_BACKOFF_S = 0.5
BUSY_BACKOFF_MAX_S = 4.0


class OllamaHTTPError(Exception):
    """Only safe categories cross the adapter boundary, never server error text."""

    def __init__(
        self,
        reason: FailureReason,
        status_code: int | None = None,
        *,
        phase: str = "unknown",
        retry_after_s: float | None = None,
    ) -> None:
        self.reason = reason
        self.status_code = status_code
        self.phase = phase
        self.retry_after_s = retry_after_s
        super().__init__(f"Ollama HTTP request failed ({reason})")


@dataclass(frozen=True)
class ChatResponse:
    content: str = field(repr=False)
    done_reason: str
    model: str
    prompt_eval_count: int | None
    eval_count: int | None
    load_duration: int | None
    prompt_eval_duration: int | None
    eval_duration: int | None
    total_duration: int | None
    output_sha256: str
    status_code: int = 200


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError("Non-finite JSON number")


def _json(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant
        )
        if not isinstance(value, dict):
            raise ValueError
        return cast(dict[str, object], value)
    except (ValueError, UnicodeError, RecursionError):
        raise OllamaHTTPError("internal") from None


def _count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise OllamaHTTPError("internal")
    return value


def _duration_ns(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise OllamaHTTPError("internal")
    return value


def _retry_after_seconds(value: str | None) -> float | None:
    """Read a delta-seconds header; an HTTP date or junk falls back to the backoff."""
    if value is None:
        return None
    text = value.strip()
    if not text.isascii() or not text.isdecimal() or len(text) > 9:
        return None
    return float(text)


def busy_delay_s(
    attempt: int,
    retry_after_s: float | None,
    *,
    backoff_s: float = BUSY_BACKOFF_S,
    maximum_s: float = BUSY_BACKOFF_MAX_S,
) -> float:
    """Honor the server's hint or double the backoff, never exceeding the cap."""
    delay = retry_after_s if retry_after_s is not None else backoff_s * 2 ** (attempt - 1)
    return min(delay, maximum_s)


class OllamaHTTPClient:
    """Use one close-only connection per request with no proxies or redirects.

    Only a queue-full 503 is sent again: Ollama refused it before inference, so no
    chunk was delivered and the model did no work. Retries share the caller's timeout.
    """

    def __init__(
        self,
        url: str,
        *,
        first_chunk_timeout_s: float = 120,
        idle_timeout_s: float = 60,
        max_metadata_bytes: int = 1024 * 1024,
        max_output_bytes: int = 4 * 1024 * 1024,
        max_line_bytes: int = 64 * 1024,
        max_stream_bytes: int = 8 * 1024 * 1024,
        busy_attempts: int = BUSY_ATTEMPTS,
        busy_backoff_s: float = BUSY_BACKOFF_S,
        busy_backoff_max_s: float = BUSY_BACKOFF_MAX_S,
    ) -> None:
        self.endpoint = validate_ollama_url(url)
        if type(busy_attempts) is not int or not 1 <= busy_attempts <= 8:
            raise ValueError("Invalid busy retry limit")
        for duration in (
            first_chunk_timeout_s,
            idle_timeout_s,
            busy_backoff_s,
            busy_backoff_max_s,
        ):
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (float, int))
                or not math.isfinite(duration)
                or not 0 < duration <= 7200
            ):
                raise ValueError("Invalid HTTP timeout")
        for limit in (max_metadata_bytes, max_output_bytes, max_line_bytes, max_stream_bytes):
            if type(limit) is not int or not 0 < limit <= 16 * 1024 * 1024:
                raise ValueError("Invalid HTTP size limit")
        self.first_chunk_timeout_s = first_chunk_timeout_s
        self.idle_timeout_s = idle_timeout_s
        self.max_metadata_bytes = max_metadata_bytes
        self.max_output_bytes = max_output_bytes
        self.max_line_bytes = max_line_bytes
        self.max_stream_bytes = max_stream_bytes
        self.busy_attempts = busy_attempts
        self.busy_backoff_s = busy_backoff_s
        self.busy_backoff_max_s = busy_backoff_max_s

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        *,
        timeout_s: float,
        cancelled: Callable[[], bool],
        consume: Callable[[http.client.HTTPResponse, Callable[[], None]], T],
        streaming: bool = False,
    ) -> T:
        deadline = time.monotonic() + timeout_s
        budget = timeout_s
        attempt = 1
        while True:
            try:
                return self._attempt(
                    method,
                    path,
                    body,
                    timeout_s=budget,
                    cancelled=cancelled,
                    consume=consume,
                    streaming=streaming,
                )
            except OllamaHTTPError as exc:
                if exc.status_code != _BUSY_STATUS:
                    raise
                delay = busy_delay_s(
                    attempt,
                    exc.retry_after_s,
                    backoff_s=self.busy_backoff_s,
                    maximum_s=self.busy_backoff_max_s,
                )
                if attempt >= self.busy_attempts or delay >= deadline - time.monotonic():
                    raise OllamaHTTPError("busy", _BUSY_STATUS, phase="status") from None
            self._wait_before_retry(delay, cancelled)
            budget = deadline - time.monotonic()
            attempt += 1

    @staticmethod
    def _wait_before_retry(delay_s: float, cancelled: Callable[[], bool]) -> None:
        until = time.monotonic() + delay_s
        while (left := until - time.monotonic()) > 0:
            if cancelled():
                raise OllamaHTTPError("cancelled")
            time.sleep(min(0.025, left))

    def _attempt(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        *,
        timeout_s: float,
        cancelled: Callable[[], bool],
        consume: Callable[[http.client.HTTPResponse, Callable[[], None]], T],
        streaming: bool = False,
    ) -> T:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise OllamaHTTPError("timeout")
        if cancelled():
            raise OllamaHTTPError("cancelled")
        started = time.monotonic()
        # HTTP work is isolated so the caller keeps polling control even while a
        # peer stalls headers, a partial line, or a chunked-transfer frame.
        connection = http.client.HTTPConnection(
            self.endpoint.host, self.endpoint.port, timeout=min(1.0, timeout_s)
        )
        stopped = threading.Event()
        finished = threading.Event()
        progress = [started]
        received_chunk = threading.Event()
        results: list[T] = []
        failures: list[OllamaHTTPError] = []
        sockets: list[socket.socket] = []
        phase = ["connect"]

        def made_progress() -> None:
            progress[0] = time.monotonic()
            received_chunk.set()

        def request() -> None:
            try:
                phase[0] = "connect"
                connection.connect()
                assert connection.sock is not None
                sockets.append(connection.sock)
                connection.sock.settimeout(timeout_s)
                if stopped.is_set():
                    return
                encoded = (
                    json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    if body is not None
                    else None
                )
                phase[0] = "headers"
                connection.request(
                    method,
                    path,
                    body=encoded,
                    headers={"Connection": "close", "Content-Type": "application/json"},
                )
                response = connection.getresponse()
                try:
                    if response.status != 200:
                        raise OllamaHTTPError(
                            "unavailable",
                            response.status,
                            phase="status",
                            retry_after_s=_retry_after_seconds(response.getheader("Retry-After")),
                        )
                    if response.getheader("Content-Encoding", "identity") != "identity":
                        raise OllamaHTTPError("internal", phase="headers")
                    phase[0] = "body"
                    results.append(consume(response, made_progress))
                finally:
                    response.close()
            except OllamaHTTPError as exc:
                if exc.phase == "unknown":
                    exc.phase = phase[0]
                failures.append(exc)
            except TimeoutError:
                failures.append(OllamaHTTPError("timeout", phase=phase[0]))
            except (OSError, http.client.HTTPException):
                failures.append(OllamaHTTPError("unavailable", phase=phase[0]))
            except Exception:
                failures.append(OllamaHTTPError("internal", phase=phase[0]))
            finally:
                connection.close()
                finished.set()

        thread = threading.Thread(target=request, name="deck-ollama-http", daemon=True)
        thread.start()
        failure: OllamaHTTPError | None = None
        try:
            while not finished.wait(0.025):
                now = time.monotonic()
                if cancelled():
                    failure = OllamaHTTPError("cancelled")
                    break
                idle_budget = (
                    self.idle_timeout_s if received_chunk.is_set() else self.first_chunk_timeout_s
                )
                if now - started >= timeout_s or streaming and now - progress[0] >= idle_budget:
                    failure = OllamaHTTPError("timeout", phase=phase[0])
                    break
            if failure is None and cancelled():
                failure = OllamaHTTPError("cancelled")
            if failure is None and time.monotonic() - started >= timeout_s:
                failure = OllamaHTTPError("timeout", phase=phase[0])
        except Exception:
            failure = OllamaHTTPError("internal")
        finally:
            stopped.set()
            if failure is not None:
                # shutdown interrupts file-buffer reads; close alone does not.
                for transport_socket in sockets:
                    try:
                        transport_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            thread.join(timeout=1.1)
        if failure is not None:
            raise failure from None
        if thread.is_alive() or not results and not failures:
            raise OllamaHTTPError("internal")
        if failures:
            raise failures[0] from None
        return results[0]

    def _metadata(
        self,
        path: str,
        body: dict[str, object] | None,
        *,
        timeout_s: float,
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        def consume(
            response: http.client.HTTPResponse, progress: Callable[[], None]
        ) -> dict[str, object]:
            data = response.read(self.max_metadata_bytes + 1)
            if len(data) > self.max_metadata_bytes:
                raise OllamaHTTPError("internal")
            return _json(data)

        return self._request(
            "GET" if body is None else "POST",
            path,
            body,
            timeout_s=timeout_s,
            cancelled=cancelled,
            consume=consume,
        )

    def version(
        self, *, timeout_s: float = 5, cancelled: Callable[[], bool] = lambda: False
    ) -> dict[str, object]:
        return self._metadata("/api/version", None, timeout_s=timeout_s, cancelled=cancelled)

    def tags(
        self, *, timeout_s: float = 5, cancelled: Callable[[], bool] = lambda: False
    ) -> dict[str, object]:
        return self._metadata("/api/tags", None, timeout_s=timeout_s, cancelled=cancelled)

    def show(
        self,
        model: str,
        *,
        timeout_s: float = 5,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict[str, object]:
        return self._metadata(
            "/api/show", {"model": model}, timeout_s=timeout_s, cancelled=cancelled
        )

    def chat(
        self,
        *,
        model: str,
        payload_text: str = "",
        messages: list[dict[str, object]] | None = None,
        timeout_s: float,
        cancelled: Callable[[], bool],
        format_json: bool = False,
        num_ctx: int = 4096,
        num_predict: int | None = None,
        temperature: float = 0,
        keep_alive: str | int | None = "1m",
        think: bool | None = None,
    ) -> ChatResponse:
        if messages is None:
            chat_messages: list[dict[str, object]] = [{"role": "user", "content": payload_text}]
        else:
            chat_messages = messages
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise ValueError("Invalid chat temperature")
        if not math.isfinite(float(temperature)) or not 0 <= float(temperature) <= 2:
            raise ValueError("Invalid chat temperature")
        options: dict[str, object] = {"temperature": float(temperature), "num_ctx": num_ctx}
        if num_predict is not None:
            if type(num_predict) is not int or not 1 <= num_predict <= 2**31 - 1:
                raise ValueError("Invalid num_predict")
            options["num_predict"] = num_predict
        body: dict[str, object] = {
            "model": model,
            "messages": chat_messages,
            "stream": True,
            "truncate": False,
            "shift": False,
            "options": options,
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        if format_json:
            body["format"] = "json"
        if think is not None:
            # Ollama's top-level switch for reasoning models such as Qwen3.
            body["think"] = think

        def consume(
            response: http.client.HTTPResponse, progress: Callable[[], None]
        ) -> ChatResponse:
            pending = bytearray()
            output: list[str] = []
            output_size = 0
            total_size = 0
            terminal: dict[str, object] | None = None
            while data := response.read1(4096):
                total_size += len(data)
                if total_size > self.max_stream_bytes:
                    raise OllamaHTTPError("internal")
                pending.extend(data)
                while b"\n" in pending:
                    line, _, remaining = pending.partition(b"\n")
                    pending = bytearray(remaining)
                    if len(line) > self.max_line_bytes or terminal is not None:
                        raise OllamaHTTPError("internal")
                    chunk = _json(bytes(line))
                    message = chunk.get("message")
                    if (
                        chunk.get("model") not in {model, model.removesuffix(":local")}
                        or type(chunk.get("done")) is not bool
                        or "error" in chunk
                        or chunk.get("remote_host")
                        or chunk.get("remote_model")
                        or not isinstance(message, dict)
                        or message.get("role") != "assistant"
                        or not isinstance(message.get("content"), str)
                        or message.get("tool_calls")
                        or message.get("images")
                    ):
                        raise OllamaHTTPError("internal")
                    content = message["content"]
                    # PostgreSQL JSONB rejects U+0000; fail here, not at result persistence.
                    if "\0" in content:
                        raise OllamaHTTPError("internal")
                    output_size += len(content.encode("utf-8"))
                    if output_size > self.max_output_bytes:
                        raise OllamaHTTPError("internal")
                    output.append(content)
                    progress()
                    if chunk["done"]:
                        if chunk.get("done_reason") not in {"stop", "length"}:
                            raise OllamaHTTPError("internal")
                        terminal = chunk
                    elif chunk.get("done_reason"):
                        raise OllamaHTTPError("internal")
                if len(pending) > self.max_line_bytes:
                    raise OllamaHTTPError("internal")
            if pending or terminal is None:
                raise OllamaHTTPError("internal")
            content = "".join(output)
            return ChatResponse(
                content=content,
                done_reason=str(terminal["done_reason"]),
                model=model,
                prompt_eval_count=_count(terminal.get("prompt_eval_count")),
                eval_count=_count(terminal.get("eval_count")),
                load_duration=_duration_ns(terminal.get("load_duration")),
                prompt_eval_duration=_duration_ns(terminal.get("prompt_eval_duration")),
                eval_duration=_duration_ns(terminal.get("eval_duration")),
                total_duration=_duration_ns(terminal.get("total_duration")),
                output_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

        return self._request(
            "POST",
            "/api/chat",
            body,
            timeout_s=timeout_s,
            cancelled=cancelled,
            consume=consume,
            streaming=True,
        )

    def chat_with_chunks(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        timeout_s: float,
        cancelled: Callable[[], bool],
        num_ctx: int = 4096,
        num_predict: int | None = None,
        temperature: float = 0,
        keep_alive: str | int | None = "1m",
        think: bool | None = None,
        on_chunk: Callable[[str], None],
    ) -> ChatResponse:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise ValueError("Invalid chat temperature")
        if not math.isfinite(float(temperature)) or not 0 <= float(temperature) <= 2:
            raise ValueError("Invalid chat temperature")
        options: dict[str, object] = {"temperature": float(temperature), "num_ctx": num_ctx}
        if num_predict is not None:
            if type(num_predict) is not int or not 1 <= num_predict <= 2**31 - 1:
                raise ValueError("Invalid num_predict")
            options["num_predict"] = num_predict
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "truncate": False,
            "shift": False,
            "options": options,
        }
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        if think is not None:
            body["think"] = think

        def consume(
            response: http.client.HTTPResponse, progress: Callable[[], None]
        ) -> ChatResponse:
            pending = bytearray()
            output: list[str] = []
            output_size = 0
            total_size = 0
            terminal: dict[str, object] | None = None
            while data := response.read1(4096):
                total_size += len(data)
                if total_size > self.max_stream_bytes:
                    raise OllamaHTTPError("internal")
                pending.extend(data)
                while b"\n" in pending:
                    line, _, remaining = pending.partition(b"\n")
                    pending = bytearray(remaining)
                    if len(line) > self.max_line_bytes or terminal is not None:
                        raise OllamaHTTPError("internal")
                    chunk = _json(bytes(line))
                    message = chunk.get("message")
                    if (
                        chunk.get("model") not in {model, model.removesuffix(":local")}
                        or type(chunk.get("done")) is not bool
                        or "error" in chunk
                        or chunk.get("remote_host")
                        or chunk.get("remote_model")
                        or not isinstance(message, dict)
                        or message.get("role") != "assistant"
                        or not isinstance(message.get("content"), str)
                        or message.get("tool_calls")
                        or message.get("images")
                    ):
                        raise OllamaHTTPError("internal")
                    content = message["content"]
                    if "\0" in content:
                        raise OllamaHTTPError("internal")
                    output_size += len(content.encode("utf-8"))
                    if output_size > self.max_output_bytes:
                        raise OllamaHTTPError("internal")
                    if content:
                        on_chunk(content)
                    output.append(content)
                    progress()
                    if chunk["done"]:
                        if chunk.get("done_reason") not in {"stop", "length"}:
                            raise OllamaHTTPError("internal")
                        terminal = chunk
                    elif chunk.get("done_reason"):
                        raise OllamaHTTPError("internal")
                if len(pending) > self.max_line_bytes:
                    raise OllamaHTTPError("internal")
            if pending or terminal is None:
                raise OllamaHTTPError("internal")
            content = "".join(output)
            return ChatResponse(
                content=content,
                done_reason=str(terminal["done_reason"]),
                model=model,
                prompt_eval_count=_count(terminal.get("prompt_eval_count")),
                eval_count=_count(terminal.get("eval_count")),
                load_duration=_duration_ns(terminal.get("load_duration")),
                prompt_eval_duration=_duration_ns(terminal.get("prompt_eval_duration")),
                eval_duration=_duration_ns(terminal.get("eval_duration")),
                total_duration=_duration_ns(terminal.get("total_duration")),
                output_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

        return self._request(
            "POST",
            "/api/chat",
            body,
            timeout_s=timeout_s,
            cancelled=cancelled,
            consume=consume,
            streaming=True,
        )
