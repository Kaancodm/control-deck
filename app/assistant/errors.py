"""Stable assistant error codes. They never carry user text."""

from __future__ import annotations


class AssistantError(Exception):
    """Expected assistant failure identified by a fixed code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
