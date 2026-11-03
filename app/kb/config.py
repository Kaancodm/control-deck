"""Explicit opt-in to an isolated simulation database; live stays unavailable."""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

from app.kb.contracts import KnowledgeError


def get_kb_test_database_url() -> str:
    raw = os.environ.get("DECK_KB_TEST_DATABASE_URL", "").strip()
    try:
        url = make_url(raw)
    except Exception:
        raise KnowledgeError("knowledge_test_database_required") from None
    if (
        url.drivername != "postgresql+psycopg"
        or not url.database
        or not url.database.endswith("_test")
    ):
        raise KnowledgeError("knowledge_test_database_required")
    return raw


def require_simulation() -> str:
    if os.environ.get("DECK_KB_PROFILE", "disabled") != "simulation":
        raise KnowledgeError("knowledge_live_profile_unavailable")
    return get_kb_test_database_url()
