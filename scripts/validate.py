#!/usr/bin/env python3
"""CLI validator for task.v1 and result.v1 JSON files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.core.validate import issues_as_strings, validate_result, validate_task


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("root JSON value must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("Usage: validate.py task|result <file.json>", file=sys.stderr)
        return 1

    kind, file_arg = args
    path = Path(file_arg)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    try:
        payload = _load_json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return 1

    if kind == "task":
        issues = validate_task(payload)
    elif kind == "result":
        issues = validate_result(payload)
    else:
        print("Kind must be 'task' or 'result'", file=sys.stderr)
        return 1

    if issues:
        for line in issues_as_strings(issues):
            print(line, file=sys.stderr)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
