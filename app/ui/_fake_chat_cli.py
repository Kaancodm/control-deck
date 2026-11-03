"""Synthetic CLI contract for UI tests; it cannot execute tools or call a provider."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from uuid import uuid4


def main() -> int:
    if sys.argv[1:] != ["run", "fake-local-chat"]:
        return 2
    payload = json.loads(sys.stdin.buffer.read(32769))
    draft = None
    if payload["prompt"].startswith("DRAFT "):
        draft = {
            "schema_version": "task.v1",
            "task_id": str(uuid4()),
            "parent_task_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "requested_by": "ui",
            "kind": "custom",
            "title": "Simulierter Entwurf",
            "instructions": payload["prompt"][6:],
            "inputs": [],
            "context_refs": [],
            "data_class": "local_only",
            "policy": {"require_review": True},
            "acceptance": [],
            "fallback_chain": ["ollama"],
            "labels": {},
        }
    print(
        json.dumps(
            {"answer": "Simulation: Anfrage empfangen. Keine Aktion ausgefuehrt.", "draft": draft}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
