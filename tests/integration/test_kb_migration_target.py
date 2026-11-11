"""Both knowledge migration selectors against separate disposable _test databases."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

REPOSITORY = Path(__file__).parents[2]


def _administration_url() -> str:
    raw = os.environ.get("DECK_KB_TEST_DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("DECK_KB_TEST_DATABASE_URL is required for pgvector integration")
    url = make_url(raw)
    assert url.database and url.database.endswith("_test")
    return raw


def _administer(statement: str) -> None:
    engine = create_engine(_administration_url(), poolclass=pool.NullPool)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(statement))


@pytest.fixture
def database_url() -> Iterator[str]:
    source = make_url(_administration_url())
    name = "deck_kb_target_" + uuid4().hex + "_test"
    _administer(f'CREATE DATABASE "{name}"')
    try:
        yield source.set(database=name).render_as_string(hide_password=False)
    finally:
        _administer(f'DROP DATABASE "{name}" WITH (FORCE)')


other_database_url = database_url


def _alembic(*arguments: str, **variables: str) -> subprocess.CompletedProcess[str]:
    # Only the named disposable databases reach the process; real connections stay out of reach.
    for name, value in variables.items():
        assert make_url(value).database.endswith("_test"), name
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_kb.ini", *arguments],
        cwd=REPOSITORY,
        env={"PATH": os.environ["PATH"], **variables},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_contract(url: str) -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_kb_version")) == "kb_0002"
        assert connection.execute(
            text("SELECT version, embedding_model, embedding_dimension FROM deck_kb.contract")
        ).all() == [("deck_kb.v1", "qwen3-embedding:0.6b", 1024)]
        assert set(
            connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'deck_kb'"))
        ) == {"contract", "documents", "chunks"}
        assert connection.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def _assert_untouched(url: str) -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT to_regnamespace('deck_kb')")) is None
        assert connection.scalar(text("SELECT to_regclass('alembic_kb_version')")) is None


@pytest.mark.parametrize(
    ("selector", "variable"),
    [
        (("-x", "target=test"), "DECK_KB_TEST_DATABASE_URL"),
        ((), "DECK_KB_TEST_DATABASE_URL"),
        (("-x", "target=runtime"), "DECK_KB_DATABASE_URL"),
    ],
)
def test_selector_migrates_checks_and_repeats(database_url, selector, variable):
    for _ in range(2):
        _assert_ok(_alembic(*selector, "upgrade", "head", **{variable: database_url}))
    _assert_ok(_alembic(*selector, "check", **{variable: database_url}))
    current = _alembic(*selector, "current", **{variable: database_url})
    _assert_ok(current)
    assert "kb_0002" in current.stdout
    _assert_contract(database_url)
    assert database_url not in current.stdout + current.stderr


def test_runtime_selector_never_touches_the_test_database(database_url, other_database_url):
    variables = {
        "DECK_KB_DATABASE_URL": database_url,
        "DECK_KB_TEST_DATABASE_URL": other_database_url,
    }
    _assert_ok(_alembic("-x", "target=runtime", "upgrade", "head", **variables))
    _assert_contract(database_url)
    _assert_untouched(other_database_url)


def test_test_selector_never_touches_the_runtime_database(database_url, other_database_url):
    variables = {
        "DECK_KB_DATABASE_URL": database_url,
        "DECK_KB_TEST_DATABASE_URL": other_database_url,
    }
    _assert_ok(_alembic("-x", "target=test", "upgrade", "head", **variables))
    _assert_contract(other_database_url)
    _assert_untouched(database_url)


def test_runtime_downgrade_changes_nothing(database_url):
    _assert_ok(
        _alembic("-x", "target=runtime", "upgrade", "head", DECK_KB_DATABASE_URL=database_url)
    )
    blocked = _alembic(
        "-x", "target=runtime", "downgrade", "base", DECK_KB_DATABASE_URL=database_url
    )
    assert blocked.returncode != 0
    assert "knowledge_runtime_command_unavailable" in blocked.stdout + blocked.stderr
    _assert_contract(database_url)


def test_runtime_url_without_target_changes_nothing(database_url):
    blocked = _alembic("upgrade", "head", DECK_KB_DATABASE_URL=database_url)
    assert blocked.returncode != 0
    assert "knowledge_migration_target_required" in blocked.stdout + blocked.stderr
    assert database_url not in blocked.stdout + blocked.stderr
    _assert_untouched(database_url)


def test_central_database_is_refused_and_stays_unchanged(database_url):
    blocked = _alembic(
        "-x",
        "target=runtime",
        "upgrade",
        "head",
        DECK_KB_DATABASE_URL=database_url,
        DECK_DATABASE_URL=database_url,
    )
    assert blocked.returncode != 0
    assert "knowledge_database_must_be_separate" in blocked.stdout + blocked.stderr
    assert database_url not in blocked.stdout + blocked.stderr
    _assert_untouched(database_url)
