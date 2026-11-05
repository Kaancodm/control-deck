#!/usr/bin/env bash
# Assert .claude/settings.json contains required Bash deny patterns and cover force-push variants.
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
require_pattern '"Bash(git push * -f *)"'
require_pattern '"Bash(git push * --force *)"'
require_pattern '"Bash(git push * --force-with-lease *)"'
require_pattern '"Bash(git push --force-with-lease)"'
require_pattern '"Bash(git push --force-with-lease *)"'
require_pattern '"Read(~/.ssh/**)"'
require_pattern '"Bash(chmod 777 *)"'
require_pattern '"Bash(chmod -R 777 *)"'
require_pattern '"Bash(curl *)"'

python3 <<PY
import fnmatch
import json
import sys
from pathlib import Path

settings = json.loads(Path(${SETTINGS@Q}).read_text(encoding="utf-8"))
guard_matchers = {
    tool
    for hook_group in settings["hooks"]["PreToolUse"]
    if any("guard.sh" in hook.get("command", "") for hook in hook_group["hooks"])
    for tool in hook_group["matcher"].split("|")
}
assert {"Edit", "Write", "Bash"} <= guard_matchers, "Guard must cover Bash as well as file writes"
deny = settings["permissions"]["deny"]
for operation in ("Read", "Edit", "Write"):
    for suffix in ("env", "runner"):
        assert f"{operation}(**/*.{suffix})" in deny, f"Missing {operation} protection for {suffix}"
bash_denies = [
    entry.removeprefix("Bash(").removesuffix(")")
    for entry in deny
    if entry.startswith("Bash(git push")
]

commands = [
    "git push origin --force main",
    "git push origin --force-with-lease main",
    "git push origin -f main",
    "git push --force origin main",
    "git push -f origin main",
]

def matches_any(command: str) -> bool:
    return any(fnmatch.fnmatchcase(command, pattern) for pattern in bash_denies)

for command in commands:
    if not matches_any(command):
        print(f"settings test failed: no git push deny pattern matches: {command!r}", file=sys.stderr)
        sys.exit(1)

print("settings.json: force-push command variants covered by deny patterns")
PY

echo "settings.json: required Bash deny patterns present"
