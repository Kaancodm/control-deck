"""Keep-alive parsing for assistant dialogue and task lane."""

from __future__ import annotations

import pytest

from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import DIALOGUE_KEEP_ALIVE_DEFAULT, OllamaDialogueAdapter
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.lanes.ollama_http import OllamaHTTPClient
from app.workers.lanes.ollama_keep_alive import lane_keep_alive_from_env, parse_keep_alive_value
from tests.support.ollama_stub import OllamaStub
from tests.unit.test_ollama_executor import MODEL
from tests.unit.test_ollama_executor import context as context

CHAT_DONE = [
    {
        "model": MODEL,
        "message": {"role": "assistant", "content": "x"},
        "done": True,
        "done_reason": "stop",
    }
]


@pytest.mark.parametrize(
    ("raw", "configured", "wire"),
    [
        ("0", "0", "0"),
        ("1m", "1m", "1m"),
        ("30m", "30m", "30m"),
        ("24h", "24h", "24h"),
        ("-1m", "-1m", "-1m"),
        ("-1", "-1", -1),
        ("server", "server", None),
        ("SERVER", "server", None),
    ],
)
def test_parse_keep_alive_accepts_ollama_wire_values(
    raw: str, configured: str, wire: str | int | None
) -> None:
    setting = parse_keep_alive_value(raw)
    assert setting.configured == configured
    assert setting.wire == wire


@pytest.mark.parametrize(
    "raw",
    [
        "forever",
        "25h",
        "9999h",
        "30 m",
        "1y",
        "",
    ],
)
def test_parse_keep_alive_rejects_invalid_tokens(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_keep_alive_value(raw)


def test_assistant_env_normalises_minus_one_to_json_number() -> None:
    base = {
        "DECK_ASSISTANT_OLLAMA": "1",
        "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
        "DECK_ASSISTANT_OLLAMA_MODEL": "fixture:localtest",
        "DECK_ASSISTANT_OLLAMA_KEEP_ALIVE": "-1",
    }
    adapter = OllamaDialogueAdapter.from_env(base)
    assert adapter.keep_alive == -1


def test_assistant_env_accepts_minus_one_minute_and_server_mode() -> None:
    base = {
        "DECK_ASSISTANT_OLLAMA": "1",
        "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
        "DECK_ASSISTANT_OLLAMA_MODEL": "fixture:localtest",
    }
    minus_one_min = OllamaDialogueAdapter.from_env(
        {**base, "DECK_ASSISTANT_OLLAMA_KEEP_ALIVE": "-1m"}
    )
    server_mode = OllamaDialogueAdapter.from_env(
        {**base, "DECK_ASSISTANT_OLLAMA_KEEP_ALIVE": "server"}
    )
    assert minus_one_min.keep_alive == "-1m"
    assert server_mode.keep_alive == "server"


def test_lane_keep_alive_env_default_and_invalid_fallback() -> None:
    assert lane_keep_alive_from_env({}).wire == "1m"
    assert lane_keep_alive_from_env({"DECK_OLLAMA_KEEP_ALIVE": "15m"}).wire == "15m"
    assert lane_keep_alive_from_env({"DECK_OLLAMA_KEEP_ALIVE": "bogus"}).wire == "1m"


@pytest.mark.parametrize(
    ("env_value", "expect_key", "expect_value"),
    [
        ("30m", "keep_alive", "30m"),
        ("-1", "keep_alive", -1),
        ("-1m", "keep_alive", "-1m"),
        ("server", None, None),
    ],
)
def test_http_chat_serialises_keep_alive(
    env_value: str, expect_key: str | None, expect_value: object
) -> None:
    setting = parse_keep_alive_value(env_value)
    with OllamaStub(chat_chunks=CHAT_DONE) as stub:
        OllamaHTTPClient(stub.url).chat(
            model=MODEL,
            payload_text="hello",
            timeout_s=2,
            cancelled=lambda: False,
            keep_alive=setting.wire,
        )
        body = stub.requests[-1]["body"]
        if expect_key is None:
            assert "keep_alive" not in body
        else:
            assert body[expect_key] == expect_value


@pytest.mark.parametrize(
    "env_value",
    ["-1", "-1m", "server", "2m"],
)
def test_lane_executor_sends_configured_keep_alive(
    monkeypatch: pytest.MonkeyPatch, context, env_value: str
) -> None:
    monkeypatch.setenv("DECK_OLLAMA_KEEP_ALIVE", env_value)
    with OllamaStub(models=(MODEL,)) as stub:
        OllamaExecutor(profile="live", url=stub.url).execute(context)
    body = next(request for request in stub.requests if request["path"] == "/api/chat")["body"]
    setting = parse_keep_alive_value(env_value)
    if setting.omit:
        assert "keep_alive" not in body
    else:
        assert body["keep_alive"] == setting.wire


def test_assistant_rejects_unknown_keep_alive_from_env() -> None:
    base = {
        "DECK_ASSISTANT_OLLAMA": "1",
        "DECK_OLLAMA_URL": "http://127.0.0.1:11434",
        "DECK_ASSISTANT_OLLAMA_MODEL": "fixture:localtest",
        "DECK_ASSISTANT_OLLAMA_KEEP_ALIVE": "forever",
    }
    with pytest.raises(AssistantError) as caught:
        OllamaDialogueAdapter.from_env(base)
    assert caught.value.code == "assistant_model_unconfigured"


def test_keep_alive_default_matches_dialogue_constant() -> None:
    assert parse_keep_alive_value(DIALOGUE_KEEP_ALIVE_DEFAULT).wire == "30m"
