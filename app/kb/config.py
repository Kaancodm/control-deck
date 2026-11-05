"""Explicit knowledge database selection; live adapters stay unavailable."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.engine import URL, make_url

from app.kb.contracts import KnowledgeError

RUNTIME_MIGRATION_COMMANDS = frozenset({"check", "current", "upgrade"})
_CENTRAL_DATABASE_NAMES = frozenset({"controldeck", "controldeck_test"})
_CENTRAL_DATABASE_VARIABLES = ("DECK_DATABASE_URL", "DECK_TEST_DATABASE_URL")
_LOOPBACK_HOSTS = frozenset({None, "", "localhost", "127.0.0.1", "::1"})

_runtime_migration_url: ContextVar[str | None] = ContextVar(
    "kb_runtime_migration_url", default=None
)


@dataclass(frozen=True)
class KbMigrationSelection:
    target: Literal["test", "runtime"]
    url: str = field(repr=False)


def _parse_url(raw: str, code: str) -> URL:
    try:
        url = make_url(raw)
    except Exception:
        raise KnowledgeError(code) from None
    if url.drivername != "postgresql+psycopg" or not url.database:
        raise KnowledgeError(code)
    return url


def get_kb_test_database_url() -> str:
    # Revision kb_0001 calls this getter as its guard. Only an explicit runtime
    # migration opens this scope, and that target cannot run a downgrade.
    runtime_url = _runtime_migration_url.get()
    if runtime_url is not None:
        return runtime_url
    raw = os.environ.get("DECK_KB_TEST_DATABASE_URL", "").strip()
    url = _parse_url(raw, "knowledge_test_database_required")
    if not url.database or not url.database.endswith("_test"):
        raise KnowledgeError("knowledge_test_database_required")
    return raw


def require_simulation() -> str:
    if os.environ.get("DECK_KB_PROFILE", "disabled") != "simulation":
        raise KnowledgeError("knowledge_live_profile_unavailable")
    return get_kb_test_database_url()


def _database_identity(url: URL) -> tuple[str | None, int, str | None]:
    host = "loopback" if url.host in _LOOPBACK_HOSTS else url.host
    return host, url.port or 5432, url.database


def _reject_central_database(candidate: URL) -> None:
    if candidate.database in _CENTRAL_DATABASE_NAMES:
        raise KnowledgeError("knowledge_database_must_be_separate")
    for variable in _CENTRAL_DATABASE_VARIABLES:
        raw = os.environ.get(variable, "").strip()
        if not raw:
            continue
        try:
            central = make_url(raw)
        except Exception:
            raise KnowledgeError("knowledge_central_database_invalid") from None
        if _database_identity(central) == _database_identity(candidate):
            raise KnowledgeError("knowledge_database_must_be_separate")


def select_kb_migration_target(x_arguments: Sequence[str] | None) -> KbMigrationSelection:
    """Choose one database before any connection exists; errors never contain a URL."""
    arguments = list(x_arguments or ())
    runtime_raw = os.environ.get("DECK_KB_DATABASE_URL", "").strip()
    if len(arguments) > 1:
        raise KnowledgeError("knowledge_migration_target_invalid")
    if not arguments and runtime_raw:
        raise KnowledgeError("knowledge_migration_target_required")
    if not arguments or arguments[0] == "target=test":
        raw = get_kb_test_database_url()
        _reject_central_database(_parse_url(raw, "knowledge_test_database_required"))
        return KbMigrationSelection("test", raw)
    if arguments[0] != "target=runtime":
        raise KnowledgeError("knowledge_migration_target_invalid")
    if not runtime_raw:
        raise KnowledgeError("knowledge_runtime_database_required")
    _reject_central_database(_parse_url(runtime_raw, "knowledge_runtime_database_invalid"))
    return KbMigrationSelection("runtime", runtime_raw)


def require_migration_command(selection: KbMigrationSelection, command: str | None) -> None:
    """Keep every command except the allowed ones away from the runtime target."""
    if selection.target == "runtime" and command not in RUNTIME_MIGRATION_COMMANDS:
        raise KnowledgeError("knowledge_runtime_command_unavailable")


@contextmanager
def migration_scope(selection: KbMigrationSelection) -> Iterator[None]:
    """Let existing revision guards accept the explicitly selected runtime database."""
    if selection.target != "runtime":
        yield
        return
    token = _runtime_migration_url.set(selection.url)
    try:
        yield
    finally:
        _runtime_migration_url.reset(token)
