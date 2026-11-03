#!/usr/bin/env bash
# Assert .claude/settings.json contains required Bash deny patterns.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SETTINGS="$ROOT/.claude/settings.json"

require_pattern() {
  local pattern="$1"
  if ! grep -Fq "$pattern" "$SETTINGS"; then
    echo "settings test failed: missing deny pattern: $pattern" >&2
    exit 1
  fi
}

require_pattern '"Bash(rm -f *)"'
require_pattern '"Bash(git push * -f)"'
require_pattern '"Bash(git push --force-with-lease)"'
require_pattern '"Bash(git push --force-with-lease *)"'

echo "settings.json: required Bash deny patterns present"
