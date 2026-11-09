"""Opt-in dialogue over the existing Ollama loopback client.

The adapter speaks ``/api/chat`` only after ``DECK_ASSISTANT_OLLAMA=1`` and a
validated loopback URL plus an explicit ``name:tag`` model. It never pulls a
model, never reads the runner live flag, and never records an uncensored or
installed-model attestation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence

from app.assistant.errors import AssistantError
from app.assistant.providers import ModelReply
from app.assistant.text import MESSAGE_MAX, reject_secrets, require_data_class
from app.workers.lanes.ollama_http import ChatResponse, OllamaHTTPClient, OllamaHTTPError
from app.workers.lanes.ollama_keep_alive import KeepAliveSetting, parse_keep_alive_value
from app.workers.loopback_url import LiveConfigurationError, validate_ollama_url

_log = logging.getLogger(__name__)

LOOPBACK_PREFIX = "Loopback, Identität nicht belegt."
# One already-installed CPU model can spend the first several seconds loading.
# The previous dialogue cap was 8s and collapsed every failure into one code.
# These bounds stay inside a 90s operator shell. There is no second attempt.
DIALOGUE_TIMEOUT_S = 75.0
# Owner may raise the read budget on slow CPUs; the ceiling still fits the 90s shell.
DIALOGUE_TIMEOUT_MAX_S = 85.0
# Keep the model resident between turns so turn two does not pay a cold load.
DIALOGUE_KEEP_ALIVE_DEFAULT = "30m"
DIALOGUE_NUM_CTX_DEFAULT = 4096
DIALOGUE_NUM_CTX_MIN = 1024
DIALOGUE_NUM_CTX_MAX = 8192
DIALOGUE_NUM_PREDICT_DEFAULT = 400
DIALOGUE_NUM_PREDICT_MIN = 16
DIALOGUE_NUM_PREDICT_MAX = 4096
DIALOGUE_TEMPERATURE_DEFAULT = 0.6
# Only a leading block is reasoning; a tag later in the answer is content.
_THINK_BLOCK = re.compile(r"\A\s*<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)
_THINK_TAIL = re.compile(r"\A.*?</think>", re.DOTALL | re.IGNORECASE)
DIALOGUE_FIRST_CHUNK_S = 60.0
DIALOGUE_IDLE_S = 20.0
# Ollama 0.40.14 types/model/name.go: a namespace has no dots; model and tag may.
# One slash is the namespace separator. A host, scheme, or extra slash is not a name.
_NAMESPACE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}\Z")
_NAME_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\Z")
_PHASES = {"connect", "headers", "body", "status", "unknown"}
_REASONS = {"cancelled", "timeout", "unavailable", "internal"}


def validate_model_tag(model: str) -> str:
    """Accept ``name:tag`` or one ``namespace/name:tag``.

    The value is stored and sent as a JSON string. It is not a URL path and
    not a filesystem path. ``:local`` is appended by the client, so the
    configured name must already carry its real tag and must not be a cloud
    or local source suffix.
    """
    if (
        not isinstance(model, str)
        or model != model.strip()
        or any(ord(char) < 33 or ord(char) == 127 for char in model)
        or model.count(":") != 1
        or model.count("/") > 1
        or ".." in model
        or "\\" in model
    ):
        raise AssistantError("assistant_model_unconfigured")
    namespace, _, rest = model.partition("/")
    if "/" not in model:
        namespace = ""
        rest = model
    name, tag = rest.split(":", 1)
    folded = model.casefold()
    if (
        (namespace != "" and _NAMESPACE.fullmatch(namespace) is None)
        or _NAME_PART.fullmatch(name) is None
        or _NAME_PART.fullmatch(tag) is None
        or ":cloud" in folded
        or folded.endswith("-cloud")
        or folded.endswith(":local")
    ):
        raise AssistantError("assistant_model_unconfigured")
    return model


def validate_keep_alive(value: str) -> KeepAliveSetting:
    """Accept bounded durations, keep-forever (``-1`` / ``-1m``), or ``server`` (omit field)."""
    if not isinstance(value, str):
        raise AssistantError("assistant_model_unconfigured")
    try:
        return parse_keep_alive_value(value)
    except ValueError as exc:
        raise AssistantError("assistant_model_unconfigured") from exc


def validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssistantError("assistant_model_unconfigured")
    if not 0 < float(value) <= DIALOGUE_TIMEOUT_MAX_S:
        raise AssistantError("assistant_model_unconfigured")
    return float(value)


def _bounded_int_env(
    environment: Mapping[str, str],
    key: str,
    default: int,
    *,
    min_value: int,
    max_value: int,
) -> int:
    raw = environment.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AssistantError("assistant_model_unconfigured") from exc
    if not min_value <= value <= max_value:
        raise AssistantError("assistant_model_unconfigured")
    return value


def _temperature_env(environment: Mapping[str, str]) -> float:
    raw = environment.get("DECK_ASSISTANT_OLLAMA_TEMPERATURE", "").strip()
    if not raw:
        return DIALOGUE_TEMPERATURE_DEFAULT
    try:
        value = float(raw)
    except ValueError as exc:
        raise AssistantError("assistant_model_unconfigured") from exc
    if not 0 <= value <= 2:
        raise AssistantError("assistant_model_unconfigured")
    return value


class StreamingReplySanitizer:
    """Release only filtered assistant text (no thinking blocks, no secrets)."""

    _SECRET_WINDOW = 128
    _TAG_PREFIXES = ("<think>", "</think>")

    def __init__(self) -> None:
        self._buffer = ""
        self._released = ""

    def _incomplete_tag_suffix(self, text: str) -> int:
        lowered = text.casefold()
        hold = 0
        for tag in self._TAG_PREFIXES:
            folded = tag.casefold()
            for length in range(min(len(text), len(folded) - 1), 0, -1):
                if lowered.endswith(folded[:length]):
                    hold = max(hold, length)
        last_lt = lowered.rfind("<")
        if last_lt >= 0 and ">" not in lowered[last_lt:]:
            hold = max(hold, len(text) - last_lt)
        return hold

    def _visible(self, buffer: str) -> str:
        lowered = buffer.casefold()
        open_idx = lowered.find("<think>")
        if open_idx >= 0 and "</think>" not in lowered:
            return strip_think(buffer[:open_idx])
        return strip_think(buffer)

    def feed(self, chunk: str) -> str:
        if chunk:
            reject_secrets(
                self._buffer[-self._SECRET_WINDOW :] + chunk,
                source="dialogue_reply",
            )
        self._buffer += chunk
        hold = self._incomplete_tag_suffix(self._buffer)
        safe = self._buffer[:-hold] if hold else self._buffer
        visible = self._visible(safe)
        delta = visible[len(self._released) :]
        if not delta:
            return ""
        self._released = visible
        return delta


def strip_think(content: str) -> str:
    """Drop a leading reasoning block a model emits despite ``think: false``.

    Some chat templates open ``<think>`` inside the prompt, so the reply then
    carries only the closing tag.
    """
    stripped = _THINK_BLOCK.sub("", content, count=1)
    if stripped == content and "<think>" not in content.casefold():
        stripped = _THINK_TAIL.sub("", content, count=1)
    return stripped.strip()


def normalize_history_reply(text: str) -> str:
    """Prepare stored assistant text for replay (strip prefix and thinking blocks)."""
    stripped = text.strip()
    if stripped.startswith(LOOPBACK_PREFIX):
        stripped = stripped[len(LOOPBACK_PREFIX) :].strip()
    return strip_think(stripped)


def _timing_detail(response: ChatResponse, *, elapsed_ms: int) -> dict[str, object]:
    detail: dict[str, object] = {"elapsed_ms": max(0, min(elapsed_ms, 120_000))}
    for key in (
        "prompt_eval_count",
        "eval_count",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
        "total_duration",
    ):
        value = getattr(response, key)
        if value is not None:
            detail[key] = value
    detail["done_reason"] = response.done_reason
    return detail


def _log_dialogue_turn(detail: Mapping[str, object]) -> None:
    payload = {"event": "assistant_ollama_dialogue_turn", **dict(detail)}
    _log.info("%s", json.dumps(payload, sort_keys=True))


class OllamaDialogueAdapter:
    """One loopback chat call. ``model_identity`` stays ``unverified``."""

    profile = "ollama_loopback"

    def __init__(
        self,
        *,
        url: str,
        model: str,
        client: OllamaHTTPClient | None = None,
        keep_alive: str | KeepAliveSetting = DIALOGUE_KEEP_ALIVE_DEFAULT,
        timeout_s: float = DIALOGUE_TIMEOUT_S,
        num_ctx: int = DIALOGUE_NUM_CTX_DEFAULT,
        num_predict: int = DIALOGUE_NUM_PREDICT_DEFAULT,
        temperature: float = DIALOGUE_TEMPERATURE_DEFAULT,
    ) -> None:
        self.url = url
        self.model = validate_model_tag(model)
        resolved = (
            keep_alive
            if isinstance(keep_alive, KeepAliveSetting)
            else validate_keep_alive(keep_alive)
        )
        self._keep_alive = resolved
        self.timeout_s = validate_timeout(timeout_s)
        if type(num_ctx) is not int or not DIALOGUE_NUM_CTX_MIN <= num_ctx <= DIALOGUE_NUM_CTX_MAX:
            raise AssistantError("assistant_model_unconfigured")
        if (
            type(num_predict) is not int
            or not DIALOGUE_NUM_PREDICT_MIN <= num_predict <= DIALOGUE_NUM_PREDICT_MAX
        ):
            raise AssistantError("assistant_model_unconfigured")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise AssistantError("assistant_model_unconfigured")
        if not 0 <= float(temperature) <= 2:
            raise AssistantError("assistant_model_unconfigured")
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = float(temperature)
        try:
            validate_ollama_url(url)
        except LiveConfigurationError as exc:
            raise AssistantError("assistant_model_unconfigured") from exc
        self.client = client or OllamaHTTPClient(
            url,
            first_chunk_timeout_s=min(
                self.timeout_s, max(DIALOGUE_FIRST_CHUNK_S, self.timeout_s - 10)
            ),
            idle_timeout_s=DIALOGUE_IDLE_S,
        )

    @property
    def keep_alive(self) -> str | int:
        """Configured keep-alive token (``server`` omits the field on the wire)."""
        if self._keep_alive.omit:
            return "server"
        wire = self._keep_alive.wire
        assert wire is not None
        return wire

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> OllamaDialogueAdapter:
        env = os.environ if environment is None else environment
        if env.get("DECK_ASSISTANT_OLLAMA") != "1":
            raise AssistantError("assistant_model_unconfigured")
        url = env.get("DECK_OLLAMA_URL", "").strip()
        model = env.get("DECK_ASSISTANT_OLLAMA_MODEL", "").strip()
        if not url or not model:
            raise AssistantError("assistant_model_unconfigured")
        keep_alive = env.get("DECK_ASSISTANT_OLLAMA_KEEP_ALIVE", "").strip()
        raw_timeout = env.get("DECK_ASSISTANT_OLLAMA_TIMEOUT_S", "").strip()
        try:
            timeout_s = float(raw_timeout) if raw_timeout else DIALOGUE_TIMEOUT_S
        except ValueError as exc:
            raise AssistantError("assistant_model_unconfigured") from exc
        num_ctx = _bounded_int_env(
            env,
            "DECK_ASSISTANT_OLLAMA_NUM_CTX",
            DIALOGUE_NUM_CTX_DEFAULT,
            min_value=DIALOGUE_NUM_CTX_MIN,
            max_value=DIALOGUE_NUM_CTX_MAX,
        )
        num_predict = _bounded_int_env(
            env,
            "DECK_ASSISTANT_OLLAMA_NUM_PREDICT",
            DIALOGUE_NUM_PREDICT_DEFAULT,
            min_value=DIALOGUE_NUM_PREDICT_MIN,
            max_value=DIALOGUE_NUM_PREDICT_MAX,
        )
        temperature = _temperature_env(env)
        keep_alive_setting = validate_keep_alive(keep_alive or DIALOGUE_KEEP_ALIVE_DEFAULT)
        return cls(
            url=url,
            model=model,
            keep_alive=keep_alive_setting,
            timeout_s=timeout_s,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
        )

    def complete(
        self,
        prompt: str,
        *,
        data_class: str,
        cancelled: Callable[[], bool] | None = None,
        timeout_s: float | None = None,
    ) -> ModelReply:
        messages = [{"role": "user", "content": prompt}]
        return self.complete_messages(
            messages, data_class=data_class, cancelled=cancelled, timeout_s=timeout_s
        )

    def complete_messages(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        data_class: str,
        cancelled: Callable[[], bool] | None = None,
        timeout_s: float | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelReply:
        checked = require_data_class(data_class)
        normalized: list[dict[str, object]] = []
        for item in messages:
            role = item.get("role")
            content = item.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise AssistantError("assistant_input")
            normalized.append({"role": role, "content": content})
            reject_secrets(content, source="dialogue_prompt")
        reject_secrets(json.dumps(normalized, ensure_ascii=False), source="dialogue_prompt")
        if timeout_s is None:
            timeout_s = self.timeout_s
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise AssistantError("assistant_model_unconfigured")
        if not 0 < float(timeout_s) <= DIALOGUE_TIMEOUT_MAX_S:
            raise AssistantError("assistant_model_unconfigured")
        timeout_s = min(float(timeout_s), self.timeout_s)
        stop = cancelled if cancelled is not None else (lambda: False)
        started = time.monotonic()
        try:
            if on_chunk is None:
                response = self.client.chat(
                    model=f"{self.model}:local",
                    messages=normalized,
                    timeout_s=float(timeout_s),
                    cancelled=stop,
                    num_ctx=self.num_ctx,
                    num_predict=self.num_predict,
                    temperature=self.temperature,
                    keep_alive=self._keep_alive.wire,
                    think=False,
                )
            else:
                sanitizer = StreamingReplySanitizer()

                def emit(chunk: str) -> None:
                    safe = sanitizer.feed(chunk)
                    if safe:
                        on_chunk(safe)

                response = self._chat_streaming(
                    normalized,
                    timeout_s=float(timeout_s),
                    cancelled=stop,
                    on_chunk=emit,
                )
        except OllamaHTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            raise AssistantError(
                _failure_code(exc), detail=_failure_detail(exc, elapsed_ms)
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        content = strip_think(response.content)
        if not content or "\0" in content:
            raise AssistantError(
                "assistant_model_malformed",
                detail={"class": "malformed", "phase": "body", "reason": "internal"},
            )
        reject_secrets(content, source="dialogue_reply")
        timing = _timing_detail(response, elapsed_ms=elapsed_ms)
        _log_dialogue_turn(timing)
        text = f"{LOOPBACK_PREFIX} {content[:2000]}"
        if len(text) > MESSAGE_MAX:
            text = text[:MESSAGE_MAX]
        return ModelReply(
            text=text,
            profile=self.profile,
            data_class=checked,
            profile_written=False,
            live=False,
            network_calls=0,
            uncensored_claim=False,
            loopback_calls=1,
            model_identity="unverified",
            detail=timing,
        )

    def _chat_streaming(
        self,
        messages: list[dict[str, object]],
        *,
        timeout_s: float,
        cancelled: Callable[[], bool],
        on_chunk: Callable[[str], None],
    ) -> ChatResponse:
        return self.client.chat_with_chunks(
            model=f"{self.model}:local",
            messages=messages,
            timeout_s=timeout_s,
            cancelled=cancelled,
            num_ctx=self.num_ctx,
            num_predict=self.num_predict,
            temperature=self.temperature,
            keep_alive=self._keep_alive.wire,
            think=False,
            on_chunk=on_chunk,
        )


def _failure_code(exc: OllamaHTTPError) -> str:
    if exc.reason == "cancelled":
        return "assistant_cancelled"
    if exc.status_code == 404:
        return "assistant_model_missing"
    if exc.status_code is not None:
        return "assistant_model_http"
    if exc.reason == "timeout" and exc.phase == "connect":
        return "assistant_model_connect_timeout"
    if exc.reason == "timeout":
        return "assistant_model_read_timeout"
    if exc.reason == "unavailable" and exc.phase == "connect":
        return "assistant_model_connect_failed"
    if exc.reason == "internal":
        return "assistant_model_malformed"
    return "assistant_model_unavailable"


_FAILURE_CLASS = {
    "assistant_cancelled": "cancelled",
    "assistant_model_missing": "model_missing",
    "assistant_model_http": "http_status",
    "assistant_model_connect_timeout": "connect_timeout",
    "assistant_model_read_timeout": "read_timeout",
    "assistant_model_connect_failed": "connect_failed",
    "assistant_model_malformed": "malformed",
    "assistant_model_unavailable": "unavailable",
}


def _failure_detail(exc: OllamaHTTPError, elapsed_ms: int) -> dict[str, object]:
    code = _failure_code(exc)
    detail: dict[str, object] = {
        "class": _FAILURE_CLASS[code],
        "phase": exc.phase if exc.phase in _PHASES else "unknown",
        "reason": exc.reason if exc.reason in _REASONS else "internal",
        "elapsed_ms": max(0, min(elapsed_ms, 120_000)),
    }
    if type(exc.status_code) is int and 100 <= exc.status_code <= 599:
        detail["http_status"] = exc.status_code
    return detail
