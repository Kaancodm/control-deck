"""Maintenance tools use an explicit search path without inheriting credentials."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.operations import restic, service
from app.operations.backup import postgres_environment
from app.operations.environment import operation_path
from app.operations.errors import OperationError
from app.operations.process import run_command


@pytest.mark.parametrize("tool", ["uv", "restic", "pg_dump", "pg_restore"])
def test_configured_tool_directory_is_used_without_inheriting_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    binary = tmp_path / tool
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "assert 'SYNTHETIC_PRIVATE_VALUE' not in os.environ\n"
        "print('configured-tool')\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("DECK_OPERATIONS_PATH", str(tmp_path))
    monkeypatch.setenv("SYNTHETIC_PRIVATE_VALUE", "must-not-be-forwarded")
    environment = service.service_environment()
    assert environment["PATH"] == str(tmp_path)
    assert "SYNTHETIC_PRIVATE_VALUE" not in environment
    assert postgres_environment("postgresql:///synthetic_test")["PATH"] == str(tmp_path)
    with (tmp_path / "output").open("wb") as output:
        run_command((tool,), env=environment, output_fd=output.fileno())
    assert (tmp_path / "output").read_text() == "configured-tool\n"


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "bin",
        "/bin:",
        ":/bin",
        "/bin::/usr/bin",
        "/bin/../sbin",
        "/bin\n/private",
        "/bin\x00/private",
    ],
)
def test_invalid_operation_path_fails_closed_without_echoing_values(path):
    with pytest.raises(OperationError) as error:
        operation_path({"DECK_OPERATIONS_PATH": path})
    assert error.value.code == "invalid_operations_path"
    assert str(error.value) == "Maintenance operation failed."


def test_default_operation_path_does_not_inherit_arbitrary_parent_path(monkeypatch):
    monkeypatch.delenv("DECK_OPERATIONS_PATH", raising=False)
    monkeypatch.setenv("PATH", "synthetic-untrusted-relative-directory")
    selected = service.service_environment()["PATH"]
    assert selected == "/usr/local/bin:/usr/bin:/bin"
    assert all(Path(part).is_absolute() for part in selected.split(os.pathsep))


def test_restic_uses_configured_tool_and_private_temporary_cache_without_home_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = [tmp_path / name for name in ("backups", "runs", "configuration", "tools")]
    for directory in roots:
        directory.mkdir(mode=0o700)
    backup_root, runs_root, private, binaries = roots
    credential = private / "password"
    credential.write_text("synthetic-fixture")
    credential.chmod(0o600)
    marker = tmp_path / "observed-cache"
    tool = binaries / "restic"
    tool.write_text(
        f"#!{sys.executable}\n"
        "import os, stat\n"
        "from pathlib import Path\n"
        "assert 'HOME' not in os.environ\n"
        "assert 'SYNTHETIC_PRIVATE_VALUE' not in os.environ\n"
        "cache = Path(os.environ['RESTIC_CACHE_DIR'])\n"
        "assert stat.S_IMODE(cache.stat().st_mode) == 0o700\n"
        "(cache / 'synthetic-entry').write_text('local-only')\n"
        f"Path({str(marker)!r}).write_text(str(cache))\n"
    )
    tool.chmod(0o700)
    monkeypatch.setenv("SYNTHETIC_PRIVATE_VALUE", "not-forwarded")
    monkeypatch.setattr(restic, "create_engine", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr(restic, "Session", MagicMock())
    monkeypatch.setattr(restic, "verified_sets", lambda *args: [object()])
    phases = []
    monkeypatch.setattr(
        restic, "record_operation", lambda *args, **kwargs: phases.append(kwargs["phase"])
    )
    restic.copy_with_restic(
        "unused",
        backup_root=backup_root,
        runs_root=runs_root,
        configuration={
            "DECK_OPERATIONS_PATH": str(binaries),
            "RESTIC_REPOSITORY": "synthetic-repository",
            "RESTIC_PASSWORD_FILE": str(credential),
        },
    )
    assert phases == ["started", "completed"]
    assert marker.is_file()
    assert not Path(marker.read_text()).exists()
