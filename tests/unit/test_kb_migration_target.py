"""Target selection for the knowledge migration chain; no network or database access."""

from __future__ import annotations

import pytest

from app.kb.config import (
    KbMigrationSelection,
    get_kb_test_database_url,
    migration_scope,
    require_migration_command,
    select_kb_migration_target,
)
from app.kb.contracts import KnowledgeError

SECRET = "s3cret-value"
TEST_URL = f"postgresql+psycopg://user:{SECRET}@secret-host:5433/knowledge_test"
RUNTIME_URL = f"postgresql+psycopg://user:{SECRET}@secret-host:5433/knowledge"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "DECK_ALEMBIC_TARGET",
        "DECK_DATABASE_URL",
        "DECK_KB_DATABASE_URL",
        "DECK_KB_TEST_DATABASE_URL",
        "DECK_TEST_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _error_code(arguments) -> str:
    with pytest.raises(KnowledgeError) as error:
        select_kb_migration_target(arguments)
    for text in (str(error.value), repr(error.value), error.value.code):
        assert SECRET not in text
        assert "secret-host" not in text
    return error.value.code


@pytest.mark.parametrize(
    ("arguments", "test_url", "runtime_url", "target", "selected"),
    [
        (None, TEST_URL, "", "test", TEST_URL),
        ([], TEST_URL, "", "test", TEST_URL),
        (["target=test"], TEST_URL, "", "test", TEST_URL),
        (["target=test"], TEST_URL, RUNTIME_URL, "test", TEST_URL),
        (["target=runtime"], "", RUNTIME_URL, "runtime", RUNTIME_URL),
        (["target=runtime"], TEST_URL, RUNTIME_URL, "runtime", RUNTIME_URL),
    ],
)
def test_target_selects_exactly_one_variable(
    monkeypatch, arguments, test_url, runtime_url, target, selected
):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", test_url)
    monkeypatch.setenv("DECK_KB_DATABASE_URL", runtime_url)
    selection = select_kb_migration_target(arguments)
    assert (selection.target, selection.url) == (target, selected)
    assert SECRET not in repr(selection)


@pytest.mark.parametrize("test_url", ["", TEST_URL])
def test_runtime_variable_without_explicit_target_is_rejected(monkeypatch, test_url):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", test_url)
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    assert _error_code(None) == "knowledge_migration_target_required"


def test_environment_cannot_choose_the_runtime_target(monkeypatch):
    monkeypatch.setenv("DECK_ALEMBIC_TARGET", "runtime")
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", TEST_URL)
    assert select_kb_migration_target(None).target == "test"
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    assert _error_code(None) == "knowledge_migration_target_required"


@pytest.mark.parametrize(
    "arguments",
    [
        ["target="],
        ["target"],
        ["target=RUNTIME"],
        ["target=runtime,test"],
        ["target=production"],
        ["other=1"],
        ["target=test", "target=test"],
        ["target=test", "target=runtime"],
        ["target=runtime", "target=runtime"],
        ["target=runtime", "other=1"],
    ],
)
def test_unknown_or_repeated_targets_are_rejected(monkeypatch, arguments):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    assert _error_code(arguments) == "knowledge_migration_target_invalid"


def test_missing_urls_are_rejected():
    assert _error_code(None) == "knowledge_test_database_required"
    assert _error_code(["target=test"]) == "knowledge_test_database_required"
    assert _error_code(["target=runtime"]) == "knowledge_runtime_database_required"


@pytest.mark.parametrize(
    "url",
    [
        f"not a url {SECRET}",
        f"sqlite:///{SECRET}",
        f"postgresql://user:{SECRET}@secret-host/knowledge",
        f"postgresql+psycopg://user:{SECRET}@secret-host/",
    ],
)
def test_invalid_runtime_urls_are_rejected_without_details(monkeypatch, url):
    monkeypatch.setenv("DECK_KB_DATABASE_URL", url)
    assert _error_code(["target=runtime"]) == "knowledge_runtime_database_invalid"


def test_test_target_still_requires_a_test_database_name(monkeypatch):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", RUNTIME_URL)
    assert _error_code(["target=test"]) == "knowledge_test_database_required"


@pytest.mark.parametrize(
    ("variable", "central"),
    [
        ("DECK_DATABASE_URL", RUNTIME_URL),
        ("DECK_TEST_DATABASE_URL", RUNTIME_URL),
        ("DECK_DATABASE_URL", "postgresql://other:pw@secret-host:5433/knowledge"),
    ],
)
def test_central_database_is_rejected_as_runtime_target(monkeypatch, variable, central):
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    monkeypatch.setenv(variable, central)
    assert _error_code(["target=runtime"]) == "knowledge_database_must_be_separate"


@pytest.mark.parametrize(
    ("central_host", "knowledge_host"),
    [("localhost", "127.0.0.1"), ("127.0.0.1", "[::1]")],
)
def test_loopback_aliases_count_as_the_same_server(monkeypatch, central_host, knowledge_host):
    monkeypatch.setenv("DECK_DATABASE_URL", f"postgresql+psycopg://u@{central_host}/shared")
    monkeypatch.setenv(
        "DECK_KB_DATABASE_URL", f"postgresql+psycopg://u@{knowledge_host}:5432/shared"
    )
    assert _error_code(["target=runtime"]) == "knowledge_database_must_be_separate"


def test_central_database_is_rejected_as_test_target(monkeypatch):
    monkeypatch.setenv("DECK_KB_TEST_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("DECK_TEST_DATABASE_URL", TEST_URL)
    assert _error_code(None) == "knowledge_database_must_be_separate"


@pytest.mark.parametrize("name", ["controldeck", "controldeck_test"])
def test_central_database_names_are_rejected_without_central_variables(monkeypatch, name):
    monkeypatch.setenv("DECK_KB_DATABASE_URL", f"postgresql+psycopg://u@secret-host/{name}")
    assert _error_code(["target=runtime"]) == "knowledge_database_must_be_separate"


def test_separate_database_on_the_same_server_is_allowed(monkeypatch):
    monkeypatch.setenv("DECK_DATABASE_URL", "postgresql+psycopg://u@secret-host:5433/central")
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    assert select_kb_migration_target(["target=runtime"]).url == RUNTIME_URL


def test_unreadable_central_url_is_rejected_without_details(monkeypatch):
    monkeypatch.setenv("DECK_DATABASE_URL", f"not a url {SECRET}")
    monkeypatch.setenv("DECK_KB_DATABASE_URL", RUNTIME_URL)
    assert _error_code(["target=runtime"]) == "knowledge_central_database_invalid"


@pytest.mark.parametrize("command", ["upgrade", "check", "current"])
def test_runtime_target_allows_listed_commands(command):
    require_migration_command(KbMigrationSelection("runtime", RUNTIME_URL), command)


@pytest.mark.parametrize("command", ["downgrade", "stamp", "revision", "merge", None])
def test_runtime_target_rejects_every_other_command(command):
    with pytest.raises(KnowledgeError) as error:
        require_migration_command(KbMigrationSelection("runtime", RUNTIME_URL), command)
    assert error.value.code == "knowledge_runtime_command_unavailable"


def test_test_target_keeps_every_command():
    require_migration_command(KbMigrationSelection("test", TEST_URL), "downgrade")


def test_revision_guard_accepts_runtime_only_inside_its_scope():
    with pytest.raises(KnowledgeError):
        get_kb_test_database_url()
    with migration_scope(KbMigrationSelection("runtime", RUNTIME_URL)):
        assert get_kb_test_database_url() == RUNTIME_URL
    with pytest.raises(KnowledgeError):
        get_kb_test_database_url()


def test_revision_guard_scope_closes_after_an_error():
    with pytest.raises(RuntimeError), migration_scope(KbMigrationSelection("runtime", RUNTIME_URL)):
        raise RuntimeError
    with pytest.raises(KnowledgeError):
        get_kb_test_database_url()


def test_test_selection_does_not_open_the_revision_guard():
    with migration_scope(KbMigrationSelection("test", TEST_URL)), pytest.raises(KnowledgeError):
        get_kb_test_database_url()
