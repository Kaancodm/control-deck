"""Compatibility entrypoint for the explicit maintenance command."""

from __future__ import annotations

import sys

from app.operations.main import main

if __name__ == "__main__":
    sys.argv.insert(1, "retention")
    raise SystemExit(main())
