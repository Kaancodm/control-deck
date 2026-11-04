"""Owner health follows the deployed migration graph, independent of cwd."""

from alembic.script import ScriptDirectory

from app.api.routes.health import schema_head


def test_schema_head_resolves_migrations_from_another_directory(tmp_path, monkeypatch):
    from pathlib import Path

    expected = ScriptDirectory(
        str(Path(__file__).resolve().parents[2] / "alembic")
    ).get_current_head()
    monkeypatch.chdir(tmp_path)
    assert schema_head() == expected


def test_health_head_tracks_new_migration_without_a_code_change(monkeypatch):
    monkeypatch.setattr(ScriptDirectory, "get_current_head", lambda self: "synthetic_next_revision")
    assert schema_head() == "synthetic_next_revision"
