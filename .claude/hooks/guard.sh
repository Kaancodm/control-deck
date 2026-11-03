#!/usr/bin/env bash
# PreToolUse hook for Edit|Write: refuse writes to secret-bearing paths.
# Exit 2 blocks the tool call; the message on stderr is shown to Claude.
# Fail closed: invalid input or missing python3 always blocks.
set -u

if ! command -v python3 >/dev/null 2>&1; then
  echo "guard.sh: python3 is required to validate writes; blocking" >&2
  exit 2
fi

GUARD_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
export GUARD_PROJECT_DIR

input="$(cat)"
export GUARD_HOOK_INPUT="$input"

result="$(python3 <<'PY'
import json
import os
import sys

project = os.path.realpath(os.environ.get("GUARD_PROJECT_DIR", os.getcwd()))
raw = os.environ.get("GUARD_HOOK_INPUT", "")

try:
    data = json.loads(raw)
except json.JSONDecodeError:
    print("INVALID_JSON")
    sys.exit(0)

tool_input = data.get("tool_input")
if not isinstance(tool_input, dict):
    print("MISSING_TOOL_INPUT")
    sys.exit(0)

file_path = tool_input.get("file_path")
if not isinstance(file_path, str) or not file_path.strip():
    print("MISSING_FILE_PATH")
    sys.exit(0)

file_path = file_path.strip()


def is_secret_path(resolved: str) -> bool:
    name = os.path.basename(resolved)
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(".pem") or name.endswith(".key"):
        return True
    if name.startswith("id_rsa"):
        return True
    if "secrets" in resolved.split(os.sep):
        return True
    return False


candidate = file_path if os.path.isabs(file_path) else os.path.join(project, file_path)

try:
    resolved = os.path.realpath(candidate)
except OSError as exc:
    print(f"RESOLVE_ERROR\t{candidate}\t{exc}")
    sys.exit(0)

project_prefix = project + os.sep
if resolved != project and not resolved.startswith(project_prefix):
    print(f"OUTSIDE_REPO\t{file_path}\t{resolved}")
    sys.exit(0)

if is_secret_path(resolved):
    print(f"SECRET\t{file_path}\t{resolved}")
    sys.exit(0)

print("OK")
PY
)"

case "$result" in
  OK)
    exit 0
    ;;
  INVALID_JSON)
    echo "guard.sh: invalid JSON on stdin; blocking write" >&2
    exit 2
    ;;
  MISSING_TOOL_INPUT)
    echo "guard.sh: missing tool_input; blocking write" >&2
    exit 2
    ;;
  MISSING_FILE_PATH)
    echo "guard.sh: missing file_path in tool_input; blocking write" >&2
    exit 2
    ;;
  OUTSIDE_REPO*)
    echo "guard.sh: refusing write outside project root (${result#OUTSIDE_REPO	})" >&2
    exit 2
    ;;
  SECRET*)
    echo "guard.sh: refusing to write secret-bearing path (${result#SECRET	})" >&2
    exit 2
    ;;
  RESOLVE_ERROR*)
    echo "guard.sh: cannot resolve path safely (${result#RESOLVE_ERROR	}); blocking" >&2
    exit 2
    ;;
  *)
    echo "guard.sh: unexpected guard result; blocking" >&2
    exit 2
    ;;
esac
