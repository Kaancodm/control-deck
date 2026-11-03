"""Pinned text-only Codex exec protocol; parsing is not preventive tool isolation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.schemas import ErrorCode

CODEX_VERSION = "0.166.0"
CODEX_SOURCE_REVISION = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
CODEX_ARCHIVE_SHA256 = "1d44b28bc4ac9937d7d3532264baa68a507ce364956a0d4ebb856f331cdee254"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_STREAM_BYTES = 4 * 1024 * 1024
_MAX_EVENTS = 4096
_MAX_TOKENS = 1_000_000_000

# Exact synthetic error fixtures, never a substring search over untrusted output.
_ERROR_MESSAGES = {
    "Authentication required": ErrorCode.AUTH_REQUIRED,
    "Rate limit reached": ErrorCode.RATE_LIMITED,
    "Request rejected by policy": ErrorCode.POLICY_BLOCKED,
    "Service unavailable": ErrorCode.WORKER_UNAVAILABLE,
}


def codex_command(model: str) -> tuple[str, ...]:
    """Describe the pinned CLI contract; no live execution path is enabled."""
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ValueError("Invalid model identifier")
    features = (
        "{shell_tool=false,unified_exec=false,code_mode=false,code_mode_host=false,"
        "hooks=false,codex_hooks=false,plugin_hooks=false,plugins=false,apps=false,"
        "multi_agent=false,collab=false,web_search=false,browser_use=false,"
        "computer_use=false,image_generation=false,view_image=false,"
        "memory_tool=false,memories=false,skill_search=false,"
        "skill_mcp_dependency_install=false,skip_host_skill_discovery=true,"
        "unbounded_connection_retries=false}"
    )
    return (
        "codex",
        "exec",
        "--strict-config",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--model",
        model,
        '--config=forced_login_method="chatgpt"',
        '--config=approval_policy="never"',
        '--config=web_search="disabled"',
        '--config=history.persistence="none"',
        "--config=mcp_servers={}",
        "--config=project_doc_max_bytes=0",
        "--config=check_for_update_on_startup=false",
        "--config=model_providers.openai.request_max_retries=0",
        "--config=model_providers.openai.stream_max_retries=0",
        "--config=features=" + features,
        "-",
    )


@dataclass(frozen=True)
class CodexResponse:
    text: str = field(default="", repr=False)
    input_tokens: int = 0
    output_tokens: int = 0
    error: ErrorCode | None = None


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("Non-finite JSON constant")


def _keys(value: object, required: set[str], optional: set[str] | None = None) -> dict[str, object]:
    if not isinstance(value, dict) or not required <= value.keys():
        raise ValueError("Missing event fields")
    if value.keys() - required - (optional or set()):
        raise ValueError("Unknown event fields")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid event identifier")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("Invalid event text")
    # Also rejects escaped lone surrogates accepted by Python's JSON decoder.
    value.encode("utf-8")
    return value


def _usage(value: object) -> tuple[int, int]:
    usage = _keys(
        value,
        {"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"},
        {"cache_write_input_tokens"},
    )
    for count in usage.values():
        if type(count) is not int or not 0 <= count <= _MAX_TOKENS:
            raise ValueError("Invalid token count")
    input_tokens = usage["input_tokens"]
    output_tokens = usage["output_tokens"]
    assert isinstance(input_tokens, int) and isinstance(output_tokens, int)
    cached = usage["cached_input_tokens"]
    reasoning = usage["reasoning_output_tokens"]
    assert isinstance(cached, int) and isinstance(reasoning, int)
    if cached > input_tokens or reasoning > output_tokens:
        raise ValueError("Inconsistent token counts")
    return input_tokens, output_tokens


def parse_codex_stream(raw: bytes, *, returncode: int) -> CodexResponse:
    """Validate one complete turn and discard reasoning, errors and partial output."""
    try:
        if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_STREAM_BYTES:
            raise ValueError("Invalid stream size")
        lines = raw.decode("utf-8").splitlines()
        if not lines or len(lines) > _MAX_EVENTS or any(not line for line in lines):
            raise ValueError("Invalid event count")
        phase = "initial"
        items: dict[str, tuple[str, bool]] = {}
        output: str | None = None
        tokens = (0, 0)
        error: ErrorCode | None = None
        for line in lines:
            event = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("Invalid event")
            kind = event["type"]
            if phase == "terminal":
                raise ValueError("Event after terminal")
            if kind in {"error", "turn.failed"}:
                _keys(event, {"type", "message"} if kind == "error" else {"type", "error"})
                details = event if kind == "error" else _keys(event["error"], {"message"})
                if kind == "turn.failed" and phase != "turn":
                    raise ValueError("Failure outside turn")
                message = _text(details["message"])
                error = _ERROR_MESSAGES.get(message, ErrorCode.INTERNAL)
                phase = "terminal"
            elif kind == "thread.started":
                _keys(event, {"type", "thread_id"})
                _identifier(event["thread_id"])
                if phase != "initial":
                    raise ValueError("Duplicate thread")
                phase = "thread"
            elif kind == "turn.started":
                _keys(event, {"type"})
                if phase != "thread":
                    raise ValueError("Unexpected turn start")
                phase = "turn"
            elif kind in {"item.started", "item.updated", "item.completed"}:
                _keys(event, {"type", "item"})
                item = _keys(event["item"], {"id", "type", "text"})
                item_id = _identifier(item["id"])
                item_type = item["type"]
                if phase != "turn" or item_type not in {"agent_message", "reasoning"}:
                    raise ValueError("Unexpected item or forbidden tool event")
                text = _text(item["text"])
                previous = items.get(item_id)
                if previous is not None and (previous[0] != item_type or previous[1]):
                    raise ValueError("Reused completed item")
                if kind == "item.started" and previous is not None:
                    raise ValueError("Duplicate item start")
                if kind == "item.updated" and previous is None:
                    raise ValueError("Update without item start")
                completed = kind == "item.completed"
                items[item_id] = (item_type, completed)
                if item_type == "agent_message" and completed:
                    output = text
            elif kind == "turn.completed":
                _keys(event, {"type", "usage"})
                if phase != "turn" or output is None or any(not done for _, done in items.values()):
                    raise ValueError("Incomplete turn")
                tokens = _usage(event["usage"])
                phase = "terminal"
            else:
                raise ValueError("Unknown event type")
        if phase != "terminal":
            raise ValueError("Missing terminal event")
        if error is not None:
            return CodexResponse(error=error)
        if type(returncode) is not int or returncode != 0:
            raise ValueError("Unsuccessful process")
        if output is None or not output.strip():
            return CodexResponse(error=ErrorCode.ACCEPTANCE_FAILED)
        return CodexResponse(text=output, input_tokens=tokens[0], output_tokens=tokens[1])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return CodexResponse(error=ErrorCode.INTERNAL)
