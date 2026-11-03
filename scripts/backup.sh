#!/usr/bin/env bash
set -euo pipefail
exec uv run python -m app.operations.main backup "$@"
