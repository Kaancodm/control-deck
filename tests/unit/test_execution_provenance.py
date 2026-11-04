"""Execution provenance comes from trusted adapters, never activation flags."""

from types import SimpleNamespace

import pytest

from app.workers.provenance import safe_execution_metadata
from app.workers.runner import executor_profile


@pytest.mark.parametrize("profile", ["simulation", "disabled", None, "invented"])
def test_live_flag_does_not_promote_simulation(monkeypatch, profile):
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    assert executor_profile(SimpleNamespace(profile=profile)) == "simulation"


def test_only_executor_attests_live():
    assert executor_profile(SimpleNamespace(profile="live")) == "live"
    assert executor_profile(SimpleNamespace()) == "simulation"


def test_only_bounded_metadata_enters_audit_and_logs():
    assert safe_execution_metadata(
        {
            "transport": "ollama_http",
            "model": "fixture:tag",
            "adapter_version": "1",
            "ollama_version": "0.40.14",
            "done_reason": "stop",
            "output_sha256": "a" * 64,
            "prompt_eval_count": 2,
            "eval_count": 3,
            "duration_ms": 100,
            "http_status": 200,
            "prompt": "PRIVATE-PROMPT",
            "answer": "PRIVATE-ANSWER",
            "error": "PRIVATE-ERROR",
            "payload_sha256": "spoofed",
            "log_ref": "spoofed",
        }
    ) == {
        "transport": "ollama_http",
        "model": "fixture:tag",
        "adapter_version": "1",
        "ollama_version": "0.40.14",
        "done_reason": "stop",
        "output_sha256": "a" * 64,
        "prompt_eval_count": 2,
        "eval_count": 3,
        "duration_ms": 100,
        "http_status": 200,
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("transport", "PRIVATE"),
        ("done_reason", "PRIVATE"),
        ("model", "bad\nmodel"),
        ("adapter_version", "PRIVATE"),
        ("ollama_version", "PRIVATE"),
        ("output_sha256", "PRIVATE"),
        ("eval_count", True),
        ("eval_count", -1),
        ("prompt_eval_count", 2**64),
        ("http_status", 999),
        ("duration_ms", float("inf")),
    ],
)
def test_invalid_metadata_is_discarded(key, value):
    assert safe_execution_metadata({key: value}) == {}
