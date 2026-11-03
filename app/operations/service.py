"""Explicit user-service plans with guarded installation and fast-forward updates."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from app.operations.backup import create_backup
from app.operations.errors import OperationError
from app.operations.files import private_root, read_file, write_file
from app.operations.process import run_command

UNITS = (
    "deck-api.service",
    "deck-runner@.service",
    "deck-backup.service",
    "deck-backup.timer",
    "deck-retention.service",
    "deck-retention.timer",
    "deck-restore-probe.service",
    "deck-restore-probe.timer",
)
_SERVICES = (
    "deck-api.service",
    "deck-runner@ollama.service",
    "deck-runner@claude.service",
    "deck-runner@codex.service",
)
_TIMERS = ("deck-backup.timer", "deck-retention.timer", "deck-restore-probe.timer")


def _template_path(path: Path) -> str:
    absolute = str(path.absolute())
    if ".." in path.parts or re.fullmatch(r"/[A-Za-z0-9_./-]+", absolute) is None:
        raise OperationError("unsupported_installation_path")
    return absolute


def render_units(repository: Path, environment_file: Path) -> dict[str, bytes]:
    """Render fixed templates without shell expansion or untrusted systemd directives."""
    replacements = {
        "@REPOSITORY@": _template_path(repository),
        "@ENVIRONMENT@": _template_path(environment_file),
        "@PYTHON@": _template_path(repository / ".venv" / "bin" / "python"),
    }
    result = {}
    template_root = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    for name in UNITS:
        content = (template_root / name).read_text(encoding="utf-8")
        for marker, value in replacements.items():
            content = content.replace(marker, value)
        if re.search(r"@[A-Z_]+@", content):
            raise OperationError("invalid_unit_template")
        result[name] = content.encode()
    return result


def install_units(repository: Path, environment_file: Path, destination: Path) -> None:
    """Create units without replacing existing user files, and never start services."""
    rendered = render_units(repository, environment_file)
    with private_root(environment_file.parent) as configuration:
        read_file(configuration, environment_file.name, maximum=65536)
    with private_root(destination, create=True) as directory:
        for name, expected in rendered.items():
            try:
                existing = read_file(directory, name, maximum=65536)
            except FileNotFoundError:
                continue
            if existing != expected:
                raise OperationError("unit_conflict")
        for name, data in rendered.items():
            try:
                write_file(directory, name, data)
            except FileExistsError:
                if read_file(directory, name, maximum=65536) != data:
                    raise OperationError("unit_conflict") from None
        os.fsync(directory)


def service_environment() -> dict[str, str]:
    """Allow only values required to reach the invoking user's service manager."""
    allowed = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    return {"PATH": os.defpath, **{key: os.environ[key] for key in allowed if key in os.environ}}


def service_command(action: str) -> tuple[str, ...]:
    """Keep service actions and instances fixed; no user-provided unit or shell fragment."""
    if action in {"start", "stop"}:
        return ("systemctl", "--user", action, *_SERVICES, *_TIMERS)
    if action == "status":
        return ("systemctl", "--user", "--no-pager", "status", *_SERVICES, *_TIMERS)
    if action == "reload":
        return ("systemctl", "--user", "daemon-reload")
    raise OperationError("invalid_service_action")


def _read_command(command: tuple[str, ...], environment: dict[str, str]) -> bytes:
    with tempfile.TemporaryFile() as stream:
        run_command(command, env=environment, output_fd=stream.fileno(), maximum_output=65536)
        stream.seek(0)
        return stream.read(65536)


def update_checkout(repository: Path, *, database_url: str, backup_root: Path) -> None:
    """Backup before mutation; require a clean main branch and only fast-forward updates."""
    root = _template_path(repository)
    environment = service_environment()
    if _read_command(("git", "-C", root, "status", "--porcelain"), environment).strip():
        raise OperationError("dirty_checkout")
    if (
        _read_command(("git", "-C", root, "branch", "--show-current"), environment).strip()
        != b"main"
    ):
        raise OperationError("unsupported_update_branch")
    create_backup(database_url, backup_root)
    run_command(("git", "-C", root, "fetch", "origin"), env=environment, maximum_output=65536)
    run_command(
        ("git", "-C", root, "merge", "--ff-only", "origin/main"),
        env=environment,
        maximum_output=65536,
    )
    run_command(
        ("uv", "sync", "--frozen", "--project", root),
        env=environment,
        maximum_output=65536,
        cwd=repository,
    )
    runtime = {**environment, "DECK_DATABASE_URL": database_url}
    run_command(
        (
            str(repository / ".venv" / "bin" / "python"),
            "-m",
            "alembic",
            "-c",
            str(repository / "alembic.ini"),
            "upgrade",
            "head",
        ),
        env=runtime,
        maximum_output=65536,
        cwd=repository,
    )
    run_command(
        ("systemctl", "--user", "restart", *_SERVICES), env=environment, maximum_output=65536
    )


def service_status() -> dict[str, str]:
    """Expose only enumerated unit states, never journal lines or environment values."""
    states = {}
    for unit in (*_SERVICES, *_TIMERS):
        value = (
            _read_command(
                ("systemctl", "--user", "show", "--property=ActiveState", "--value", unit),
                service_environment(),
            )
            .decode()
            .strip()
        )
        if value not in {"active", "inactive", "failed", "activating", "deactivating", "reloading"}:
            raise OperationError("invalid_service_status")
        states[unit] = value
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or explicitly apply user-service changes")
    parser.add_argument("action", choices=("install", "start", "stop", "status", "update"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--unit-directory", type=Path)
    arguments = parser.parse_args()
    if not arguments.apply:
        print(json.dumps({"action": arguments.action, "status": "dry_run"}))
        return 0
    try:
        if arguments.action == "install":
            if arguments.environment_file is None or arguments.unit_directory is None:
                raise OperationError("installation_paths_required")
            install_units(
                arguments.repository, arguments.environment_file, arguments.unit_directory
            )
            run_command(service_command("reload"), env=service_environment(), maximum_output=65536)
            run_command(
                ("systemctl", "--user", "enable", *_TIMERS),
                env=service_environment(),
                maximum_output=65536,
            )
        elif arguments.action == "update":
            from app.db.config import get_database_url

            update_checkout(
                arguments.repository,
                database_url=get_database_url(),
                backup_root=Path(os.environ.get("DECK_BACKUP_DIR", "backups")),
            )
        elif arguments.action == "status":
            print(json.dumps({"units": service_status()}))
            return 0
        else:
            run_command(
                service_command(arguments.action), env=service_environment(), maximum_output=65536
            )
        print(json.dumps({"action": arguments.action, "status": "completed"}))
        return 0
    except Exception as error:
        reason = error.code if isinstance(error, OperationError) else "service_operation_failed"
        print(json.dumps({"status": "failed", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
