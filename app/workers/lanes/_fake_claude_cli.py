"""Synthetic Claude 2.1.317 result fixtures; no provider calls or semantic reference cases."""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    arguments = sys.argv[1:]
    if not arguments or arguments[0] != "-p" or "--bare" in arguments:
        return 2
    if os.environ["DECK_SIMULATION_CLI_VERSION"] != "2.1.317":
        return 2
    payload = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024:
        return 2
    behavior = os.environ["DECK_SIMULATION_BEHAVIOR"]
    if behavior == "delay":
        time.sleep(float(os.environ["DECK_SIMULATION_DELAY"]))
    if behavior == "invalid_json":
        print("synthetic invalid response")
        return 0
    if behavior == "invalid_utf8":
        sys.stdout.buffer.write(b"\xff")
        return 0
    if behavior in {"stdout_limit", "stderr_limit"}:
        stream = sys.stdout if behavior == "stdout_limit" else sys.stderr
        stream.write("x" * (5 * 1024 * 1024))
        return 0
    if behavior == "duplicate_keys":
        print('{"type":"result","is_error":true,"is_error":false}')
        return 0
    text = payload.decode("utf-8") if behavior == "echo" else os.environ["DECK_SIMULATION_OUTPUT"]
    result: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "terminal_reason": "completed",
        "result": text,
        "permission_denials": [],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    codes = {"auth": 401, "policy": 403, "rate_limit": 429, "unavailable": 503}
    if behavior in codes or behavior in {"internal", "error_zero"}:
        result.update(is_error=True, result="", api_error_status=codes.get(behavior, 500))
        if behavior in {"internal", "error_zero"}:
            result.pop("api_error_status")
        result["errors"] = ["Synthetic provider failure"]
        print(json.dumps(result))
        return 0 if behavior == "error_zero" else 1
    if behavior == "empty":
        result["result"] = ""
    if behavior == "incomplete_zero":
        result["terminal_reason"] = "max_turns"
    if behavior == "permission_denied":
        result["permission_denials"] = [{"tool_name": "Bash", "tool_input": "synthetic"}]
    if behavior == "negative_usage":
        result["usage"] = {"input_tokens": -1, "output_tokens": 3}
    if behavior == "boolean_usage":
        result["usage"] = {"input_tokens": True, "output_tokens": 3}
    if behavior == "wrong_type":
        result["is_error"] = "false"
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
