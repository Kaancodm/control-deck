"""Task lane context cap from DECK_OLLAMA_TASK_NUM_CTX."""

from __future__ import annotations

import pytest

from app.workers.lanes.ollama import task_num_ctx_cap


def test_task_num_ctx_defaults_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECK_OLLAMA_TASK_NUM_CTX", raising=False)
    assert task_num_ctx_cap() == 4096


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2048", 2048),
        ("8192", 8192),
        ("not-a-number", 4096),
        ("0", 4096),
        ("999999", 4096),
    ],
)
def test_task_num_ctx_env_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("DECK_OLLAMA_TASK_NUM_CTX", raw)
    assert task_num_ctx_cap() == expected
