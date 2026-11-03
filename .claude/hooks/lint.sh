#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: auto-fix ruff findings on the changed Python file.
# Never blocks; lint failures surface via `make lint`.
set -u

input="$(cat)"
file_path="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(data.get("tool_input", {}).get("file_path", ""))
' 2>/dev/null)"

case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

[ -f "$file_path" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0
uv run ruff check --fix "$file_path" >&2 || true
exit 0
