"""Service tooling plans safely and renders only fixed, private unit files."""

from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.operations import service
from app.operations.errors import OperationError


def _configuration(tmp_path: Path) -> Path:
    parent = tmp_path / "configuration"
    parent.mkdir(mode=0o700)
    path = parent / "env"
    path.write_text("DECK_EXAMPLE=simulation\n")
    path.chmod(0o600)
    return path


def test_install_is_idempotent_and_never_starts_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service, "run_command", lambda *args, **kwargs: pytest.fail("No service call")
    )
    destination = tmp_path / "units"
    environment = _configuration(tmp_path)
    service.install_units(tmp_path / "repository", environment, destination)
    service.install_units(tmp_path / "repository", environment, destination)
    assert {path.name for path in destination.iterdir()} == set(service.UNITS)
    assert "app.api.server_main" in (destination / "deck-api.service").read_text()
    runner = (destination / "deck-runner@.service").read_text()
    assert "RestartPreventExitStatus=78" in runner
    assert "--simulate" not in runner


def test_unit_templates_have_valid_offline_syntax(tmp_path: Path) -> None:
    environment = tmp_path / "env"
    environment.write_text("")
    templates = service.render_units(Path.cwd(), environment)
    paths = []
    for name, content in templates.items():
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(content.decode())
        assert "Unit" in parser and ("Service" in parser or "Timer" in parser)
        if "Service" in parser:
            assert parser["Service"]["UMask"] == "0077"
            assert parser["Service"]["NoNewPrivileges"] == "true"
        destination = tmp_path / name
        destination.write_bytes(content)
        paths.append(str(destination))
    verifier = shutil.which("systemd-analyze")
    if verifier:
        result = subprocess.run(
            [verifier, "verify", "--man=no", *paths], capture_output=True, timeout=15, check=False
        )
        assert result.returncode == 0


def test_install_refuses_conflicting_units(tmp_path: Path) -> None:
    environment = _configuration(tmp_path)
    destination = tmp_path / "units"
    destination.mkdir(mode=0o700)
    existing = destination / "deck-api.service"
    existing.write_text("owner customization")
    existing.chmod(0o600)
    with pytest.raises(OperationError) as error:
        service.install_units(tmp_path / "repository", environment, destination)
    assert error.value.code == "unit_conflict"
    assert len(list(destination.iterdir())) == 1


@pytest.mark.parametrize(
    "path", ["space path", "line\nbreak", "percent%i", "dollar$HOME", "semi;colon"]
)
def test_unit_rendering_refuses_directive_injection(tmp_path: Path, path: str) -> None:
    with pytest.raises(OperationError):
        service.render_units(tmp_path / path, tmp_path / "env")


@pytest.mark.parametrize("action", ["install", "start", "stop", "status", "update"])
def test_service_defaults_are_plans(
    action: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(sys, "argv", ["service", action])
    monkeypatch.setattr(service, "run_command", lambda *args, **kwargs: pytest.fail("No process"))
    assert service.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"


@pytest.mark.parametrize(
    "dirty,branch,expected",
    [
        (b" M modified\n", b"main", "dirty_checkout"),
        (b"", b"stage/example", "unsupported_update_branch"),
    ],
)
def test_update_checks_before_backup_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty: bytes, branch: bytes, expected: str
) -> None:
    monkeypatch.setattr(
        service, "_read_command", lambda command, env: dirty if "status" in command else branch
    )
    monkeypatch.setattr(service, "create_backup", lambda *args, **kwargs: pytest.fail("No backup"))
    monkeypatch.setattr(service, "run_command", lambda *args, **kwargs: pytest.fail("No mutation"))
    with pytest.raises(OperationError) as error:
        service.update_checkout(tmp_path, database_url="unused", backup_root=tmp_path / "backup")
    assert error.value.code == expected


def test_update_backup_precedes_mutation_and_failure_prevents_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        service, "_read_command", lambda command, env: b"" if "status" in command else b"main"
    )
    monkeypatch.setattr(service, "create_backup", lambda *args, **kwargs: calls.append("backup"))

    def command(args, **kwargs):
        calls.append(args)
        if "upgrade" in args:
            raise OperationError("migration_failed")

    monkeypatch.setattr(service, "run_command", command)
    with pytest.raises(OperationError):
        service.update_checkout(tmp_path, database_url="unused", backup_root=tmp_path / "backup")
    assert calls[0] == "backup"
    assert any("--ff-only" in call for call in calls[1:])
    assert not any("restart" in call for call in calls[1:])


def test_status_exposes_only_allowlisted_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_read_command", lambda command, env: b"active\n")
    assert set(service.service_status().values()) == {"active"}
    monkeypatch.setattr(service, "_read_command", lambda command, env: b"private raw diagnostic")
    with pytest.raises(OperationError):
        service.service_status()


def test_update_requires_complete_backup_before_service_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service, "_read_command", lambda command, env: b"" if "status" in command else b"main"
    )
    monkeypatch.setattr(service, "run_command", lambda *a, **k: pytest.fail("No mutation"))
    with pytest.raises(OperationError) as error:
        service.update_checkout(
            tmp_path,
            database_url="postgresql://example/central_test",
            backup_root=tmp_path / "backup",
        )
    assert error.value.code == "knowledge_database_required"


def test_update_threads_knowledge_database_to_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service, "_read_command", lambda command, env: b"" if "status" in command else b"main"
    )
    seen = []
    monkeypatch.setattr(service, "create_backup", lambda *a, **k: seen.append(k))
    monkeypatch.setattr(service, "run_command", lambda *a, **k: None)
    service.update_checkout(
        tmp_path,
        database_url="synthetic-central",
        knowledge_url="synthetic-kb",
        backup_root=tmp_path,
    )
    assert seen == [{"knowledge_url": "synthetic-kb"}]


def test_restore_timer_is_only_a_weekly_dry_run_reminder(tmp_path):
    rendered = service.render_units(Path.cwd(), tmp_path / "env")
    timer = configparser.ConfigParser(interpolation=None)
    timer.read_string(rendered["deck-restore-probe.timer"].decode())
    unit = configparser.ConfigParser(interpolation=None)
    unit.read_string(rendered["deck-restore-probe.service"].decode())
    assert timer["Timer"]["OnCalendar"] == "weekly"
    assert "--apply" not in unit["Service"]["ExecStart"]
    assert unit["Service"]["ExecStart"].endswith("app.operations.main restore-probe")
