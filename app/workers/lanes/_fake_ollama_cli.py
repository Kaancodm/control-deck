"""Bundled offline fixture for the Ollama CLI protocol; it never imports providers."""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    models = json.loads(os.environ.get("DECK_SIMULATION_MODELS", "[]"))
    arguments = sys.argv[1:]
    if arguments == ["list"]:
        print("NAME ID SIZE MODIFIED")
        for model in models:
            print(f"{model} SIMULATED 0 simulation")
        return 0
    if len(arguments) != 2 or arguments[0] != "run" or arguments[1] not in models:
        print("Simulation model unavailable", file=sys.stderr)
        return 1
    payload = sys.stdin.buffer.read()
    behavior = os.environ.get("DECK_SIMULATION_BEHAVIOR", "ok")
    if behavior == "delay":
        time.sleep(float(os.environ.get("DECK_SIMULATION_DELAY", "0")))
    if behavior == "echo":
        sys.stdout.buffer.write(payload)
    elif behavior in {"empty", "incomplete_zero"}:
        pass
    elif behavior == "fail":
        print("Synthetic failure output", file=sys.stderr)
        return 1
    elif behavior == "invalid_utf8":
        sys.stdout.buffer.write(b"\xff")
    elif behavior == "stdout_limit":
        sys.stdout.buffer.write(b"x" * (5 * 1024 * 1024))
    elif behavior == "stderr_limit":
        sys.stderr.buffer.write(b"x" * (1024 * 1024))
    else:
        sys.stdout.buffer.write(os.environ.get("DECK_SIMULATION_OUTPUT", "simulation-ok").encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
