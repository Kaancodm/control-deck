"""Shared relative path rules for task inputs and worker outputs."""

from __future__ import annotations

import re

# Relative deck paths: no leading slash, no backslashes, no home prefix, no .. segments.
DECK_RELATIVE_PATH = re.compile(
    r"^(?![/\\~])(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)


def validate_deck_relative_path(value: str, *, field: str) -> str:
    if not DECK_RELATIVE_PATH.fullmatch(value):
        raise ValueError(
            f"{field} must be a relative path without '..', leading '/', backslashes, or '~'"
        )
    return value
