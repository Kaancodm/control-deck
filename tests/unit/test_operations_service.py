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


def _update_scenario(monkeypatch, *, failing_step=None, recovery_failures=()):
    previous = "a" * 40
    state = {"revision": previous, "failed": False, "recovering": False}
    active = ("deck-api.service", "deck-runner@ollama.service")
    commands = []
    events = []

    def read(command, environment):
        if "status" in command:
            return b""
        if "branch" in command:
            return b"main"
        if "rev-parse" in command:
            commands.append(("saved_revision", state["revision"]))
            return state["revision"].encode()
        raise AssertionError("Unexpected command")

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "git":
            step = next(part for part in ("fetch", "merge", "checkout") if part in command)
        elif command[0] == "uv":
            step = "sync"
        elif command[0] == "systemctl":
            step = command[2]
        else:
            step = "migration"
        if state["recovering"] and step in recovery_failures:
            raise OperationError("simulated_recovery_failure")
        if step == "merge":
            state["revision"] = "b" * 40
        if step == "checkout":
            state["revision"] = command[-1]
        if step == failing_step and not state["failed"]:
            state["failed"] = True
            state["recovering"] = True
            raise OperationError("simulated_update_failure")

    monkeypatch.setattr(service, "_read_command", read)
    monkeypatch.setattr(service, "run_command", run)
    monkeypatch.setattr(
        service, "create_backup", lambda *args, **kwargs: commands.append(("backup",))
    )
    monkeypatch.setattr(
        service,
        "service_status",
        lambda: {
            unit: "active" if unit in active else "inactive"
            for unit in (*service._SERVICES, *service._TIMERS)
        },
    )
    monkeypatch.setattr(
        service,
        "_record_update",
        lambda database_url, operation_id, phase, **details: events.append(
            {"operation_id": operation_id, "phase": phase, **details}
        ),
        raising=False,
    )
    return previous, active, state, commands, events


@pytest.mark.parametrize("step", ["stop", "fetch", "merge", "sync", "migration", "start"])
def test_update_failure_restores_revision_and_previously_active_units(tmp_path, monkeypatch, step):
    previous, active, state, commands, events = _update_scenario(monkeypatch, failing_step=step)
    with pytest.raises(OperationError):
        service.update_checkout(tmp_path, database_url="synthetic", backup_root=tmp_path / "backup")
    assert state["revision"] == previous
    checkout = ("git", "-C", str(tmp_path), "checkout", "--detach", previous)
    assert checkout in commands
    assert commands.index(("saved_revision", previous)) < commands.index(checkout)
    recovered = commands[commands.index(checkout) + 1 :]
    assert [
        command[-1] for command in recovered if command[:3] == ("systemctl", "--user", "start")
    ] == list(active)
    assert events[-1]["phase"] == "failed"
    assert events[-1]["reason"] == "update_failed"
    assert len({event["operation_id"] for event in events}) == 1


def test_successful_update_preserves_inactive_units(tmp_path, monkeypatch):
    previous, active, state, commands, events = _update_scenario(monkeypatch)
    service.update_checkout(tmp_path, database_url="synthetic", backup_root=tmp_path / "backup")
    assert state["revision"] != previous
    assert ("systemctl", "--user", "start", *active) in commands
    assert all("restart" not in command for command in commands)
    assert events[-1]["phase"] == "completed"


def test_update_cli_failure_exits_78_with_safe_output(tmp_path, monkeypatch, capsys):
    _update_scenario(monkeypatch, failing_step="migration")
    monkeypatch.setenv("DECK_DATABASE_URL", "synthetic")
    monkeypatch.setattr(service, "configured_knowledge_url", lambda **kwargs: "synthetic-kb")
    monkeypatch.setattr(
        sys, "argv", ["service", "update", "--apply", "--repository", str(tmp_path)]
    )
    assert service.main() == 78
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "reason": "update_failed"}


@pytest.mark.parametrize("recovery_step", ["stop", "checkout", "sync", "start"])
def test_update_recovery_failure_still_attempts_each_previously_active_unit(
    tmp_path, monkeypatch, capsys, recovery_step
):
    previous, active, _state, commands, events = _update_scenario(
        monkeypatch, failing_step="migration", recovery_failures=(recovery_step,)
    )
    monkeypatch.setenv("DECK_DATABASE_URL", "synthetic")
    monkeypatch.setattr(service, "configured_knowledge_url", lambda **kwargs: "synthetic-kb")
    monkeypatch.setattr(
        sys, "argv", ["service", "update", "--apply", "--repository", str(tmp_path)]
    )
    assert service.main() == 78
    checkout = ("git", "-C", str(tmp_path), "checkout", "--detach", previous)
    recovered = commands[commands.index(checkout) + 1 :]
    assert recovered == [
        ("uv", "sync", "--frozen", "--project", str(tmp_path)),
        *(("systemctl", "--user", "start", unit) for unit in active),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "reason": "update_rollback_failed",
    }
    assert events[-1]["phase"] == "failed"
    assert events[-1]["reason"] == "update_rollback_failed"


def test_update_backup_failure_is_audited_without_mutation(tmp_path, monkeypatch):
    _previous, _active, _state, commands, events = _update_scenario(monkeypatch)

    def backup_failure(*args, **kwargs):
        raise OperationError("simulated_backup_failure")

    monkeypatch.setattr(service, "create_backup", backup_failure)
    with pytest.raises(OperationError):
        service.update_checkout(tmp_path, database_url="synthetic", backup_root=tmp_path / "backup")
    assert commands == []
    assert [event["phase"] for event in events] == ["started", "failed"]
    assert events[-1]["reason"] == "update_failed"


@pytest.mark.parametrize("revision", ["--force", "main", "", "a" * 39])
def test_update_rejects_invalid_saved_revision_before_mutation(tmp_path, monkeypatch, revision):
    _previous, _active, state, commands, events = _update_scenario(monkeypatch)
    state["revision"] = revision
    with pytest.raises(OperationError) as error:
        service.update_checkout(tmp_path, database_url="synthetic", backup_root=tmp_path / "backup")
    assert error.value.code == "invalid_update_revision"
    assert commands == [("backup",), ("saved_revision", revision)]
    assert events[-1]["phase"] == "failed"


def test_update_with_inactive_services_does_not_start_them(tmp_path, monkeypatch):
    _previous, _active, _state, commands, events = _update_scenario(monkeypatch)
    monkeypatch.setattr(
        service, "service_status", lambda: dict.fromkeys(service._SERVICES, "inactive")
    )
    service.update_checkout(tmp_path, database_url="synthetic", backup_root=tmp_path / "backup")
    assert all(command[0] != "systemctl" for command in commands)
    assert events[-1]["phase"] == "completed"


def _configuration(tmp_path: Path) -> Path:
    (tmp_path / "repository").mkdir(mode=0o700)
    parent = tmp_path / "configuration"
    parent.mkdir(mode=0o700)
    path = parent / "env"
    path.write_text("DECK_EXAMPLE=simulation\n")
    path.chmod(0o600)
    runner = parent / "env.runner"
    runner.write_text("DECK_RUNS_DIR=runs\n")
    runner.chmod(0o600)
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


def test_unit_templates_have_valid_offline_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        # Installation and every service action target the user manager. A system
        # verifier incorrectly requires access to the host's system runtime path.
        runtime = tmp_path / "runtime"
        runtime.mkdir(mode=0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.setenv("LC_ALL", "C")
        command = [verifier, "--user", "verify", "--man=no"]
        result = subprocess.run([*command, *paths], capture_output=True, timeout=15, check=False)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")

        # A negative control proves the real verifier still rejects broken units.
        invalid = tmp_path / "invalid.service"
        invalid.write_bytes(
            b"\n".join(
                line
                for line in templates["deck-api.service"].splitlines()
                if not line.startswith(b"ExecStart=")
            )
            + b"\n"
        )
        rejected = subprocess.run(
            [*command, str(invalid)], capture_output=True, timeout=15, check=False
        )
        assert rejected.returncode != 0
        assert b"ExecStart" in rejected.stderr


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
    _update_scenario(monkeypatch)
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
    _previous, _active, _state, calls, _events = _update_scenario(
        monkeypatch, failing_step="migration"
    )
    with pytest.raises(OperationError):
        service.update_checkout(tmp_path, database_url="unused", backup_root=tmp_path / "backup")
    assert calls[0] == ("backup",)
    assert any("--ff-only" in call for call in calls[1:])
    assert not any("restart" in call for call in calls[1:])


def test_status_exposes_only_allowlisted_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_read_command", lambda command, env: b"active\n")
    assert set(service.service_status().values()) == {"active"}
    monkeypatch.setattr(service, "_read_command", lambda command, env: b"private raw diagnostic")
    with pytest.raises(OperationError):
        service.service_status()


def test_update_requires_complete_backup_before_service_mutation(tmp_path, monkeypatch):
    create_backup = service.create_backup
    _update_scenario(monkeypatch)
    monkeypatch.setattr(service, "create_backup", create_backup)
    monkeypatch.setattr(service, "run_command", lambda *a, **k: pytest.fail("No mutation"))
    with pytest.raises(OperationError) as error:
        service.update_checkout(
            tmp_path,
            database_url="postgresql://example/central_test",
            backup_root=tmp_path / "backup",
        )
    assert error.value.code == "knowledge_database_required"


def test_update_threads_knowledge_database_to_backup(tmp_path, monkeypatch):
    _update_scenario(monkeypatch)
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


def test_runner_unit_uses_own_environment_file_without_api_secrets(tmp_path):
    api_env = tmp_path / "api.env"
    runner_env = tmp_path / "runner.env"
    rendered = service.render_units(Path.cwd(), api_env, runner_env)
    runner = rendered["deck-runner@.service"].decode()
    assert f"EnvironmentFile={runner_env}" in runner
    assert str(api_env) not in runner
    assert "DECK_OWNER_TOKEN" not in runner and "DECK_SECRET_KEY" not in runner
    assert f"EnvironmentFile={api_env}" in rendered["deck-api.service"].decode()
    assert str(runner_env) not in rendered["deck-api.service"].decode()


def test_runner_env_example_contains_only_daemon_allowlist():
    from app.workers.daemon import _ENVIRONMENT

    lines = (Path.cwd() / "deploy" / "runner.env.example").read_text().splitlines()
    names = {line.split("=")[0] for line in lines if line and not line.startswith("#")}
    assert names == set(_ENVIRONMENT)


def test_install_requires_private_runner_environment_file(tmp_path):
    environment = _configuration(tmp_path)
    (environment.parent / "env.runner").unlink()
    with pytest.raises((OperationError, FileNotFoundError)):
        service.install_units(tmp_path / "repository", environment, tmp_path / "units")
