"""Synthetic Codex 0.166.0 JSONL fixtures, without provider calls or generated code."""

from __future__ import annotations

import json
import os
import sys
import time


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main() -> int:
    arguments = sys.argv[1:]
    if (
        not arguments
        or arguments[0] != "exec"
        or arguments[-1] != "-"
        or any(
            flag not in arguments
            for flag in ("--json", "--ephemeral", "--strict-config", "--ignore-user-config")
        )
        or "workspace-write" in arguments
        or os.environ["DECK_SIMULATION_CLI_VERSION"] != "0.166.0"
    ):
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
        print('{"type":"error","type":"thread.started"}')
        return 0
    emit({"type": "thread.started", "thread_id": "synthetic-thread"})
    emit({"type": "turn.started"})
    failures = {
        "auth": "Authentication required",
        "policy": "Request rejected by policy",
        "rate_limit": "Rate limit reached",
        "unavailable": "Service unavailable",
        "internal": "Synthetic internal failure",
        "error_zero": "Synthetic internal failure",
    }
    if behavior in failures:
        emit({"type": "turn.failed", "error": {"message": failures[behavior]}})
        return 0 if behavior == "error_zero" else 1
    if behavior == "permission_denied":
        emit(
            {
                "type": "item.completed",
                "item": {
                    "id": "tool_1",
                    "type": "command_execution",
                    "command": "synthetic",
                    "aggregated_output": "",
                    "exit_code": None,
                    "status": "declined",
                },
            }
        )
    text: object = (
        payload.decode("utf-8") if behavior == "echo" else os.environ["DECK_SIMULATION_OUTPUT"]
    )
    if behavior == "empty":
        text = ""
    if behavior == "wrong_type":
        text = 42
    emit(
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}}
    )
    if behavior == "incomplete_zero":
        return 0
    usage: dict[str, object] = {
        "input_tokens": 7,
        "cached_input_tokens": 0,
        "output_tokens": 3,
        "reasoning_output_tokens": 0,
    }
    if behavior == "negative_usage":
        usage["input_tokens"] = -1
    if behavior == "boolean_usage":
        usage["input_tokens"] = True
    emit({"type": "turn.completed", "usage": usage})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
