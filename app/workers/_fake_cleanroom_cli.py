"""Offline container command fixture. This script does not provide isolation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["info", "--format", "{{json .SecurityOptions}}"]:
        print(
            json.dumps(["name=rootless"] if os.environ["DECK_SIMULATION_ROOTLESS"] == "1" else [])
        )
        return 0
    if len(arguments) == 3 and arguments[:2] == ["rm", "--force"]:
        return int(os.environ["DECK_SIMULATION_CLEANUP_FAILS"])
    if not arguments or arguments[0] != "run":
        return 2
    try:
        cli = min(arguments.index(name) for name in ("claude", "codex") if name in arguments)
    except ValueError:
        return 2
    fake_cli = str(Path(__file__).with_name("lanes") / f"_fake_{arguments[cli]}_cli.py")
    os.execve(sys.executable, [sys.executable, fake_cli, *arguments[cli + 1 :]], dict(os.environ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
