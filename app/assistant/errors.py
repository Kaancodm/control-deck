"""Stable assistant error codes. They never carry user text."""

from __future__ import annotations

from collections.abc import Mapping

MODEL_BUSY_CODE = "assistant_model_busy"
MODEL_BUSY_TEXT = ("Ollama ausgelastet.", "Die Warteschlange ist voll. Kurz warten und neu senden.")


class AssistantError(Exception):
    """Expected assistant failure identified by a fixed code.

    ``detail`` holds only sanitised tokens. ``str(self)`` stays the code.
    """

    def __init__(self, code: str, *, detail: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.detail: dict[str, object] = {} if detail is None else dict(detail)
        super().__init__(code)
