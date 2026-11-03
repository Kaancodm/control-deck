"""Pinned Codex JSONL completion and rejection contracts using synthetic events."""

import json

import pytest

from app.core.schemas import ErrorCode
from app.workers.lanes.codex_protocol import codex_command, parse_codex_stream


def events(text="synthetic output"):
    return [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 8,
                "cached_input_tokens": 2,
                "output_tokens": 4,
                "reasoning_output_tokens": 1,
            },
        },
    ]


def encoded(items):
    return "\n".join(json.dumps(item) for item in items).encode()


def test_complete_turn_preserves_text_and_usage():
    outcome = parse_codex_stream(encoded(events(" exact\nUnicode: ☀ ")), returncode=0)
    assert outcome.error is None
    assert outcome.text == " exact\nUnicode: ☀ "
    assert (outcome.input_tokens, outcome.output_tokens) == (8, 4)
    assert "Unicode" not in repr(outcome)


def test_reasoning_is_validated_but_never_returned():
    data = events()
    data.insert(
        2,
        {
            "type": "item.completed",
            "item": {"id": "reason_1", "type": "reasoning", "text": "private reasoning"},
        },
    )
    outcome = parse_codex_stream(encoded(data), returncode=0)
    assert outcome.error is None
    assert "reasoning" not in outcome.text
    assert "reasoning" not in repr(outcome)


def test_progress_updates_and_last_completed_message():
    data = events()
    data[2:2] = [
        {"type": "item.started", "item": {"id": "item_2", "type": "agent_message", "text": ""}},
        {
            "type": "item.updated",
            "item": {"id": "item_2", "type": "agent_message", "text": "partial"},
        },
        {
            "type": "item.completed",
            "item": {"id": "item_2", "type": "agent_message", "text": "earlier"},
        },
    ]
    assert parse_codex_stream(encoded(data), returncode=0).text == "synthetic output"


@pytest.mark.parametrize(
    "item_type",
    [
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "todo_list",
        "error",
        "future_tool",
    ],
)
def test_tool_or_unknown_items_are_rejected_even_on_exit_zero(item_type):
    data = events()
    data.insert(
        2,
        {
            "type": "item.completed",
            "item": {"id": "tool_1", "type": item_type, "text": "untrusted"},
        },
    )
    assert parse_codex_stream(encoded(data), returncode=0).error == ErrorCode.INTERNAL


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_thread",
        "missing_turn",
        "missing_terminal",
        "duplicate_terminal",
        "trailing_item",
        "duplicate_item",
        "unfinished_item",
        "update_without_start",
        "duplicate_thread",
        "empty_turn",
        "unknown_event",
        "extra_field",
        "wrong_type",
    ],
)
def test_incomplete_or_ambiguous_streams_fail_closed(mutation):
    data = events()
    if mutation == "missing_thread":
        data.pop(0)
    elif mutation == "missing_turn":
        data.pop(1)
    elif mutation == "missing_terminal":
        data.pop()
    elif mutation == "duplicate_terminal":
        data.append(data[-1])
    elif mutation == "trailing_item":
        data.append(data[2])
    elif mutation == "duplicate_item":
        data.insert(3, data[2])
    elif mutation == "unfinished_item":
        data[2]["type"] = "item.started"
    elif mutation == "update_without_start":
        data[2]["type"] = "item.updated"
    elif mutation == "duplicate_thread":
        data.insert(1, data[0])
    elif mutation == "empty_turn":
        data.pop(2)
    elif mutation == "unknown_event":
        data.insert(2, {"type": "unknown"})
    elif mutation == "extra_field":
        data[0]["unexpected"] = "ignored?"
    elif mutation == "wrong_type":
        data[2]["item"]["text"] = 42
    outcome = parse_codex_stream(encoded(data), returncode=0)
    assert outcome.error == ErrorCode.INTERNAL
    assert outcome.text == ""


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "cache_write_input_tokens",
    ],
)
@pytest.mark.parametrize("value", [-1, True, 2.5, "1", None, 1_000_000_001])
def test_usage_is_bounded_strict_integer(field, value):
    data = events()
    data[-1]["usage"][field] = value
    assert parse_codex_stream(encoded(data), returncode=0).error == ErrorCode.INTERNAL


@pytest.mark.parametrize(
    "message,code",
    [
        ("Authentication required", ErrorCode.AUTH_REQUIRED),
        ("Rate limit reached", ErrorCode.RATE_LIMITED),
        ("Request rejected by policy", ErrorCode.POLICY_BLOCKED),
        ("Service unavailable", ErrorCode.WORKER_UNAVAILABLE),
        ("unknown sensitive error", ErrorCode.INTERNAL),
        ("The document says Authentication required", ErrorCode.INTERNAL),
    ],
)
@pytest.mark.parametrize("returncode", [0, 1])
def test_errors_use_exact_safe_codes_without_returning_provider_text(message, code, returncode):
    data = events()[:2] + [{"type": "turn.failed", "error": {"message": message}}]
    response = parse_codex_stream(encoded(data), returncode=returncode)
    assert response.error == code
    assert response.text == ""
    assert message not in repr(response)


def test_fatal_auth_error_can_precede_thread_start():
    response = parse_codex_stream(
        encoded([{"type": "error", "message": "Authentication required"}]), returncode=1
    )
    assert response.error == ErrorCode.AUTH_REQUIRED


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"null",
        b"{}",
        b'{"type":"error","type":"thread.started"}',
        b'{"type":NaN}',
        b"[[]]",
        b"\n",
        b"{}\n\n",
        b"x" * (4 * 1024 * 1024 + 1),
    ],
)
def test_invalid_encoding_json_and_size(raw):
    assert parse_codex_stream(raw, returncode=0).error == ErrorCode.INTERNAL


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_blank_completed_text_is_acceptance_failure(text):
    assert (
        parse_codex_stream(encoded(events(text)), returncode=0).error == ErrorCode.ACCEPTANCE_FAILED
    )


def test_nonzero_exit_cannot_publish_apparent_success():
    assert parse_codex_stream(encoded(events()), returncode=1).error == ErrorCode.INTERNAL


def test_command_uses_exact_stdin_read_only_subscription_profile():
    command = codex_command("synthetic-model")
    assert command[:2] == ("codex", "exec")
    assert command[-1] == "-"
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command and "--strict-config" in command
    assert '--config=forced_login_method="chatgpt"' in command
    assert "--config=model_providers.openai.request_max_retries=0" in command
    assert "workspace-write" not in command


@pytest.mark.parametrize(
    "model", ["", "--help", "path/model", "a b", "x\n", "x\0", "a" * 129, None, 42]
)
def test_unsafe_model_rejected(model):
    with pytest.raises(ValueError, match="Invalid model"):
        codex_command(model)
