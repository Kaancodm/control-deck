"""Explicit knowledge database selection; live adapters stay unavailable."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.engine import URL, make_url

from app.assistant.errors import AssistantError
from app.assistant.ollama_chat import validate_model_tag
from app.kb.contracts import (
    FAKE_EMBEDDING_SPEC,
    SCHEMA_EMBEDDING_DIMENSION,
    EmbeddingSpec,
    KnowledgeError,
)
from app.workers.lanes.ollama_keep_alive import (
    KEEP_ALIVE_SERVER,
    KeepAliveSetting,
    parse_keep_alive_value,
)
from app.workers.loopback_url import LiveConfigurationError, validate_ollama_url

RUNTIME_MIGRATION_COMMANDS = frozenset({"check", "current", "upgrade"})
# One budget for evicting the chat model, loading, and computing; below the 90s shell.
EMBEDDING_TIMEOUT_DEFAULT_S = 60.0
EMBEDDING_TIMEOUT_MIN_S = 1.0
EMBEDDING_TIMEOUT_MAX_S = 85.0
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


@dataclass(frozen=True)
class EmbeddingSettings:
    """Validated before any database or HTTP connection exists."""

    spec: EmbeddingSpec
    ollama_url: str | None = field(repr=False)
    timeout_s: float
    keep_alive: KeepAliveSetting


def _invalid() -> KnowledgeError:
    return KnowledgeError("knowledge_embedding_configuration_invalid")


def load_embedding_spec(environment: Mapping[str, str] | None = None) -> EmbeddingSpec:
    """Read provider, model and width; the fake takes no model or width of its own."""
    env = os.environ if environment is None else environment
    provider = env.get("DECK_KB_EMBEDDING_PROVIDER", "").strip() or "fake"
    model = env.get("DECK_KB_EMBEDDING_MODEL", "").strip()
    raw_dimension = env.get("DECK_KB_EMBEDDING_DIMENSION", "").strip()
    if provider == "fake":
        if model or raw_dimension:
            raise _invalid()
        return FAKE_EMBEDDING_SPEC
    if provider != "ollama" or not model or not raw_dimension:
        raise _invalid()
    try:
        validate_model_tag(model)
    except AssistantError:
        raise _invalid() from None
    if not raw_dimension.isascii() or not raw_dimension.isdecimal():
        raise _invalid()
    if int(raw_dimension) != SCHEMA_EMBEDDING_DIMENSION:
        raise _invalid()
    return EmbeddingSpec("ollama", model, SCHEMA_EMBEDDING_DIMENSION)


def load_embedding_settings(environment: Mapping[str, str] | None = None) -> EmbeddingSettings:
    """Fail closed on any invalid value; an Ollama spec never degrades to the fake."""
    env = os.environ if environment is None else environment
    spec = load_embedding_spec(env)
    raw_timeout = env.get("DECK_KB_EMBEDDING_TIMEOUT_S", "").strip()
    try:
        timeout_s = float(raw_timeout) if raw_timeout else EMBEDDING_TIMEOUT_DEFAULT_S
    except ValueError:
        raise _invalid() from None
    if not EMBEDDING_TIMEOUT_MIN_S <= timeout_s <= EMBEDDING_TIMEOUT_MAX_S:
        raise _invalid()
    try:
        keep_alive = parse_keep_alive_value(
            env.get("DECK_KB_EMBEDDING_KEEP_ALIVE", "").strip() or KEEP_ALIVE_SERVER
        )
    except ValueError:
        raise _invalid() from None
    if spec.provider == "fake":
        return EmbeddingSettings(spec, None, timeout_s, keep_alive)
    url = env.get("DECK_OLLAMA_URL", "").strip()
    try:
        validate_ollama_url(url)
    except LiveConfigurationError:
        raise _invalid() from None
    return EmbeddingSettings(spec, url, timeout_s, keep_alive)


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
