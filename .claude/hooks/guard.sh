#!/usr/bin/env bash
# PreToolUse hook for Edit|Write: refuse writes to secret-bearing paths.
# Exit 2 blocks the tool call; the message on stderr is shown to Claude.
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

[ -z "$file_path" ] && exit 0

base="$(basename -- "$file_path")"
case "$base" in
  .env|.env.*|*.pem|*.key)
    echo "guard.sh: refusing to write secret-bearing file '$file_path'" >&2
    exit 2
    ;;
esac

case "/$file_path" in
  */secrets/*)
    echo "guard.sh: refusing to write inside a secrets/ directory ('$file_path')" >&2
    exit 2
    ;;
esac

exit 0
