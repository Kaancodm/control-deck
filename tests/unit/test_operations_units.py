"""Backup sandbox paths follow private runtime configuration without shell evaluation."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from app.operations.errors import OperationError
from app.operations.service import install_units


def configuration(tmp_path: Path, backup_value: str) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    private = tmp_path / "configuration"
    private.mkdir(mode=0o700)
    environment = private / "runtime"
    environment.write_text(f"DECK_BACKUP_DIR={backup_value}\n")
    environment.chmod(0o600)
    runner = private / "runtime.runner"
    runner.write_text("DECK_RUNS_DIR=runs\n")
    runner.chmod(0o600)
    return repository, environment


@pytest.mark.parametrize("style", ["relative", "absolute", "single_quoted", "double_quoted"])
def test_backup_unit_uses_configured_private_writable_directory(tmp_path, style):
    target = tmp_path / "archives"
    value = str(target)
    if style == "relative":
        value = "archives"
        target = tmp_path / "repository" / "archives"
    elif style == "single_quoted":
        value = f"'{value}'"
    elif style == "double_quoted":
        value = f'"{value}"'
    repository, environment = configuration(tmp_path, value)
    units = tmp_path / "units"
    install_units(repository, environment, units)
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(units / "deck-backup.service")
    assert unit["Service"]["ReadWritePaths"] == str(target)
    assert unit["Service"]["EnvironmentFile"] == str(environment)
    assert unit["Service"]["ProtectHome"] == "read-only"
    assert unit["Service"]["ProtectSystem"] == "strict"
    assert unit["Service"]["PrivateTmp"] == "true"
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "value",
    [
        "",
        "../escape",
        "$HOME/archives",
        "bad%h",
        '"unterminated',
        "archives\nDECK_BACKUP_DIR=other",
    ],
)
def test_invalid_backup_directory_configuration_never_installs_units(tmp_path, value):
    repository, environment = configuration(tmp_path, value)
    units = tmp_path / "units"
    with pytest.raises(OperationError):
        install_units(repository, environment, units)
    assert not units.exists()


def test_backup_directory_symlink_is_rejected_before_writing_units(tmp_path):
    repository, environment = configuration(tmp_path, "archives")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (repository / "archives").symlink_to(outside, target_is_directory=True)
    units = tmp_path / "units"
    with pytest.raises(OperationError):
        install_units(repository, environment, units)
    assert not list(units.glob("*.service"))
