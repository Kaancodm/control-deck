"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import get_database_url, get_engine_kwargs, get_test_database_url

_engine: Engine | None = None
_test_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_test_session_factory: sessionmaker[Session] | None = None


def get_engine(*, for_tests: bool = False) -> Engine:
    """Lazy singleton engine (runtime or isolated integration-test engine)."""
    global _engine, _test_engine, _session_factory, _test_session_factory
    if for_tests:
        if _test_engine is None:
            _test_engine = create_engine(get_test_database_url(), **get_engine_kwargs())
            _test_session_factory = sessionmaker(
                bind=_test_engine, autoflush=False, autocommit=False
            )
        return _test_engine

    if _engine is None:
        _engine = create_engine(get_database_url(), **get_engine_kwargs())
        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory(*, for_tests: bool = False) -> sessionmaker[Session]:
    """Return the configured session factory (creates the engine if needed)."""
    get_engine(for_tests=for_tests)
    if for_tests:
        assert _test_session_factory is not None
        return _test_session_factory
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    """Dispose engines so tests can rebind to a different database URL."""
    global _engine, _test_engine, _session_factory, _test_session_factory
    if _engine is not None:
        _engine.dispose()
    if _test_engine is not None:
        _test_engine.dispose()
    _engine = None
    _test_engine = None
    _session_factory = None
    _test_session_factory = None
