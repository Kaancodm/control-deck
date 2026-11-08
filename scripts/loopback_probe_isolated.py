"""Run the loopback probe with the standard library only.

From a checkout of this commit, with Python 3.12 already on the host:

    env -u PYTHONPATH -u DECK_DATABASE_URL -u DECK_LIVE_ENABLED \
      DECK_OLLAMA_URL='http://127.0.0.1:PORT' \
      DECK_ASSISTANT_OLLAMA_MODEL='name:tag' \
      python3 -S scripts/loopback_probe_isolated.py

``python3 -S`` does not import site-packages, so the project lockfile and
``opentelemetry-api`` are not required. The process then calls
``OllamaDialogueAdapter`` in this tree. It does not install packages.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _site_packages() -> list[str]:
    return [item for item in sys.path if "site-packages" in item.replace("\\", "/")]


def main() -> int:
    blocked = _site_packages()
    if blocked:
        print("python -S required", file=sys.stderr)
        return 4
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from app.assistant.loopback_probe import main as probe_main

    return probe_main()


if __name__ == "__main__":
    raise SystemExit(main())
