"""Isolated loopback probe for the same dialogue adapter.

Run on the host that already has Ollama. This module does not read a database
URL, does not migrate, does not start a queue, and does not load a secret file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import LOOPBACK_PREFIX, OllamaDialogueAdapter

SYNTHETIC_PROMPT = "Reply with the single token LOOPBACK_PROBE_7"


def probe(url: str, model: str) -> dict[str, object]:
    """Call ``OllamaDialogueAdapter`` once. The identity stays unverified."""
    adapter = OllamaDialogueAdapter(url=url, model=model)
    produced = adapter.complete(SYNTHETIC_PROMPT, data_class="local_only")
    preview = produced.text[:180]
    return {
        "profile": produced.profile,
        "model_identity": produced.model_identity,
        "uncensored_claim": produced.uncensored_claim,
        "live": produced.live,
        "network_calls": produced.network_calls,
        "loopback_calls": produced.loopback_calls,
        "prefix_ok": produced.text.startswith(LOOPBACK_PREFIX),
        "configured_model": model,
        "answer_sha256": hashlib.sha256(produced.text.encode("utf-8")).hexdigest(),
        "answer_preview": preview,
    }


def main() -> int:
    url = os.environ.get("DECK_OLLAMA_URL", "").strip()
    model = os.environ.get("DECK_ASSISTANT_OLLAMA_MODEL", "").strip()
    if not url or not model:
        print("missing DECK_OLLAMA_URL or DECK_ASSISTANT_OLLAMA_MODEL", file=sys.stderr)
        return 2
    try:
        result = probe(url, model)
    except AssistantError as exc:
        print(exc.code, file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
