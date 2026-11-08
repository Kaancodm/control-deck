"""Isolated loopback probe for the same dialogue adapter.

Run on the host that already has Ollama. This module does not read a database
URL, does not migrate, does not start a queue, and does not load a secret file.
The import closure uses the Python standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import LOOPBACK_PREFIX, OllamaDialogueAdapter

SYNTHETIC_PROMPT = "Reply with the single token LOOPBACK_PROBE_7"
_ROOT = Path(__file__).resolve().parents[2]


def implementation_hashes() -> dict[str, str]:
    """sha256 of the app modules this process actually loaded."""
    files: dict[str, str] = {}
    for name, module in sys.modules.items():
        if name != "app" and not name.startswith("app."):
            continue
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str):
            continue
        path = Path(raw).resolve()
        try:
            relative = path.relative_to(_ROOT).as_posix()
        except ValueError:
            continue
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def third_party_modules() -> list[str]:
    """Distribution names outside the standard library and this package."""
    found: set[str] = set()
    for name in sys.modules:
        root = name.split(".", 1)[0]
        if root in sys.stdlib_module_names or root in {"app", "__main__"}:
            continue
        found.add(root)
    return sorted(found)


def execution_facts() -> dict[str, object]:
    return {
        "runtime": "python-stdlib",
        "third_party_modules": third_party_modules(),
        "implementation": implementation_hashes(),
    }


def probe(url: str, model: str) -> dict[str, object]:
    """Call ``OllamaDialogueAdapter`` once. The identity stays unverified."""
    adapter = OllamaDialogueAdapter(url=url, model=model)
    produced = adapter.complete(SYNTHETIC_PROMPT, data_class="local_only")
    preview = produced.text[:180]
    report = execution_facts()
    report.update(
        {
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
    )
    return report


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
