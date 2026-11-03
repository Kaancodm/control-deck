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
  local file_path="${3:-}"
  set +e
  local out
  out="$(printf '%s' "$payload" | "$GUARD" 2>&1)"
  local code=$?
  set -e
  if [ "$code" -ne "$expect_code" ]; then
    echo "guard test failed: expected exit $expect_code got $code (path=${file_path:-n/a})" >&2
    echo "$out" >&2
    exit 1
  fi
}

payload_ok() {
  local path="$1"
  printf '{"tool_input":{"file_path":"%s"}}' "$path"
}

echo "guard.sh: invalid JSON blocks"
run_guard 2 'not-json' 'invalid-json'

echo "guard.sh: missing tool_input blocks"
run_guard 2 '{"other":{}}' 'missing-tool-input'

echo "guard.sh: missing file_path blocks"
run_guard 2 '{"tool_input":{}}' 'missing-file-path'

echo "guard.sh: normal path allowed"
run_guard 0 "$(payload_ok 'public/notes.txt')" 'public/notes.txt'
touch "$TMP/public/notes.txt"

echo "guard.sh: direct secret path blocked"
run_guard 2 "$(payload_ok 'secrets/token.txt')" 'secrets/token.txt'

echo "guard.sh: .env blocked"
run_guard 2 "$(payload_ok '.env')" '.env'

echo "guard.sh: symlink bypass blocked"
echo 'secret' >"$TMP/secrets/hidden.txt"
ln -s "$TMP/secrets/hidden.txt" "$TMP/public/link.txt"
run_guard 2 "$(payload_ok 'public/link.txt')" 'symlink-bypass'

echo "guard.sh: all cases passed"
