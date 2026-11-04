"""Explicit tool lookup for maintenance without inheriting a broad process environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import PurePosixPath

from app.operations.errors import OperationError

DEFAULT_OPERATIONS_PATH = "/usr/local/bin:/usr/bin:/bin"


def operation_path(configuration: Mapping[str, str] | None = None) -> str:
    """Allow configured uv, restic and PostgreSQL binaries, never cwd-relative lookup."""
    values = os.environ if configuration is None else configuration
    value = values.get("DECK_OPERATIONS_PATH", DEFAULT_OPERATIONS_PATH)
    parts = value.split(os.pathsep)
    if any(
        not part
        or not PurePosixPath(part).is_absolute()
        or ".." in PurePosixPath(part).parts
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        for part in parts
    ):
        raise OperationError("invalid_operations_path")
    return value
