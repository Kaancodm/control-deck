"""Load bundled JSON Schema files from the installed package."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, cast


def load_schema(name: str) -> dict[str, Any]:
    """Return a parsed schema from ``app.schemas`` (wheel-safe, no checkout path)."""
    package = resources.files("app.schemas")
    text = package.joinpath(name).read_text(encoding="utf-8")
    return cast(dict[str, Any], json.loads(text))
