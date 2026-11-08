#!/usr/bin/env bash
# PreToolUse hook for Edit|Write|Bash: refuse direct access to secret-bearing paths.
# Exit 2 blocks the tool call; the message on stderr is shown to Claude.
# Fail closed: invalid input or missing python3 always blocks.
set -u

if ! command -v python3 >/dev/null 2>&1; then
  echo "guard.sh: python3 is required to validate writes; blocking" >&2
  exit 2
fi

GUARD_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
export GUARD_PROJECT_DIR

hook_input_file="$(mktemp)"
trap 'rm -f "$hook_input_file"' EXIT
cat >"$hook_input_file"
export GUARD_HOOK_INPUT_FILE="$hook_input_file"

result="$(python3 <<'PY'
import json
import os
import re
import shlex
import sys

project = os.path.realpath(os.environ.get("GUARD_PROJECT_DIR", os.getcwd()))
input_path = os.environ.get("GUARD_HOOK_INPUT_FILE", "")
try:
    with open(input_path, encoding="utf-8") as handle:
        raw = handle.read()
except OSError as exc:
    print(f"READ_ERROR\t{exc}")
    sys.exit(0)

try:
    data = json.loads(raw)
except json.JSONDecodeError:
    print("INVALID_JSON")
    sys.exit(0)

tool_input = data.get("tool_input") if isinstance(data, dict) else None
if not isinstance(tool_input, dict):
    print("MISSING_TOOL_INPUT")
    sys.exit(0)

def is_secret_path(resolved: str) -> bool:
    name = os.path.basename(resolved)
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key", ".env", ".runner")):
        return True
    if name.startswith("id_rsa"):
        return True
    if "secrets" in resolved.split(os.sep):
        return True
    return False


if data.get("tool_name") in {"Bash", "Shell"}:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        print("INVALID_COMMAND")
        sys.exit(0)
    # This hook checks explicit paths, not arbitrary program semantics. Do not
    # pretend to resolve dynamic shell paths or pipe-fed arguments statically.
    if any(character in command for character in "$`*?[]"):
        print("DYNAMIC_COMMAND")
        sys.exit(0)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        print("INVALID_COMMAND")
        sys.exit(0)
    if any(token in {"|", "|&", "<(", ">(", "-exec", "-execdir", "xargs", "eval"} for token in tokens):
        print("DYNAMIC_COMMAND")
        sys.exit(0)
    for token in tokens:
        # Keep the complete token for quoted paths with spaces, then inspect
        # literal paths embedded in options, shell wrappers or interpreter text.
        candidates = [token, *re.split(r"[\s\"'`(){};|&<>=:]", token)]
        for candidate in candidates:
            if not candidate:
                continue
            path = candidate if os.path.isabs(candidate) else os.path.join(project, candidate)
            try:
                if is_secret_path(path) or is_secret_path(os.path.realpath(path)):
                    print("SECRET_COMMAND")
                    sys.exit(0)
            except (OSError, ValueError):
                print("INVALID_COMMAND")
                sys.exit(0)
    print("OK")
    sys.exit(0)

file_path = tool_input.get("file_path")
if not isinstance(file_path, str) or file_path == "":
    print("MISSING_FILE_PATH")
    sys.exit(0)

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

if is_secret_path(candidate) or is_secret_path(resolved):
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
  INVALID_COMMAND)
    echo "guard.sh: invalid Bash command; blocking" >&2
    exit 2
    ;;
  DYNAMIC_COMMAND)
    echo "guard.sh: cannot validate dynamic Bash paths; use explicit paths" >&2
    exit 2
    ;;
  SECRET_COMMAND)
    echo "guard.sh: refusing Bash access to a secret-bearing path" >&2
    exit 2
    ;;
  READ_ERROR*)
    echo "guard.sh: cannot read hook input (${result#READ_ERROR	}); blocking" >&2
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
