#!/usr/bin/env bash
# Shell tests for .claude/hooks/guard.sh (run in CI and locally).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$ROOT/.claude/hooks/guard.sh"
TMP="$ROOT/tests/hooks/_guard_tmp"

if [ ! -x "$GUARD" ]; then
  chmod +x "$GUARD"
fi

rm -rf "$TMP"
mkdir -p "$TMP/public" "$TMP/secrets"
export CLAUDE_PROJECT_DIR="$TMP"

run_guard() {
  local expect_code="$1"
  local payload="$2"
  local label="${3:-}"
  set +e
  local out
  out="$(printf '%s' "$payload" | "$GUARD" 2>&1)"
  local code=$?
  set -e
  if [ "$code" -ne "$expect_code" ]; then
    echo "guard test failed: expected exit $expect_code got $code (${label:-n/a})" >&2
    echo "$out" >&2
    exit 1
  fi
}

payload_with_path() {
  python3 -c 'import json, sys; print(json.dumps({"tool_input": {"file_path": sys.argv[1]}}))' "$1"
}

echo "guard.sh: invalid JSON blocks"
run_guard 2 'not-json' 'invalid-json'

echo "guard.sh: missing tool_input blocks"
run_guard 2 '{"other":{}}' 'missing-tool-input'

echo "guard.sh: missing file_path blocks"
run_guard 2 '{"tool_input":{}}' 'missing-file-path'

echo "guard.sh: normal path allowed"
run_guard 0 "$(payload_with_path 'public/notes.txt')" 'public/notes.txt'
touch "$TMP/public/notes.txt"

echo "guard.sh: direct secret path blocked"
run_guard 2 "$(payload_with_path 'secrets/token.txt')" 'secrets/token.txt'

echo "guard.sh: .env blocked"
run_guard 2 "$(payload_with_path '.env')" '.env'

echo "guard.sh: symlink bypass blocked"
echo 'secret' >"$TMP/secrets/hidden.txt"
ln -s "$TMP/secrets/hidden.txt" "$TMP/public/link.txt"
run_guard 2 "$(payload_with_path 'public/link.txt')" 'symlink-bypass'

echo "guard.sh: trailing-space symlink path blocked (no path strip)"
echo 'dummy-secret' >"$TMP/secrets/dummy.txt"
ln -sf "../secrets/dummy.txt" "$TMP/public/bait.txt "
run_guard 2 "$(payload_with_path 'public/bait.txt ')" 'trailing-space-symlink'

echo "guard.sh: large hook payload (150 KiB) still validates path"
large_payload="$(python3 <<PY
import json
pad = "x" * (150 * 1024)
print(json.dumps({"tool_input": {"file_path": "public/notes.txt", "content": pad}}))
PY
)"
run_guard 0 "$large_payload" 'large-payload'

echo "guard.sh: all cases passed"
