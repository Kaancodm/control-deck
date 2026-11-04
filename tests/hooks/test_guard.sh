#!/usr/bin/env bash
# Shell tests for .claude/hooks/guard.sh (run in CI and locally).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$ROOT/.claude/hooks/guard.sh"
TMP="$ROOT/tests/hooks/_guard_tmp"
CASE_COUNT=0

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
  CASE_COUNT=$((CASE_COUNT + 1))
}

payload_with_path() {
  python3 -c 'import json, sys; print(json.dumps({"tool_input": {"file_path": sys.argv[1]}}))' "$1"
}

payload_with_command() {
  python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))' "$1"
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

echo "guard.sh: environment and runner files blocked"
for path in '.env.local' 'config/service.env' 'owner.runner'; do
  run_guard 2 "$(payload_with_path "$path")" "$path"
done

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

echo "guard.sh: ordinary Bash checks and explicit inspection allowed"
for command in 'uv run pytest tests/unit/' 'uv run ruff check .' \
  'git status --short && git diff --stat' 'cat public/notes.txt'; do
  run_guard 0 "$(payload_with_command "$command")" 'safe-bash'
done

echo "guard.sh: direct Bash reads, writes, wrappers and symlinks blocked"
for command in 'cat .env' 'cat config/service.env' 'cat owner.runner' \
  'cat .env.local' 'printf synthetic > owner.runner' 'cat < config/service.env' \
  'cp public/notes.txt .env' 'cat public/link.txt' 'cat "public/bait.txt "' \
  "cat '.e''nv'" 'bash -c "cat .env"' 'python3 -c '\''open(".env").read()'\'''; do
  run_guard 2 "$(payload_with_command "$command")" 'sensitive-bash'
done

echo "guard.sh: unresolved shell paths fail closed"
for command in 'cat "$TARGET"' 'cat $(printf .env)' 'cat `printf .env`' \
  'cat .*' 'find . -exec cat {} +' 'find . | xargs cat'; do
  run_guard 2 "$(payload_with_command "$command")" 'dynamic-bash'
done
run_guard 2 '{"tool_name":"Bash","tool_input":{}}' 'missing-command'
run_guard 2 "$(payload_with_command 'cat "')" 'unclosed-quote'

echo "guard.sh: all $CASE_COUNT cases passed"
