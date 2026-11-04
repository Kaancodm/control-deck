"""Explicit user-service plans with guarded installation and fast-forward updates."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.operations.audit import record_operation
from app.operations.backup import configured_knowledge_url, create_backup
from app.operations.environment import operation_path
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


def default_runner_environment_file(environment_file: Path) -> Path:
    """Runner configuration sits beside the API file but is a separate, smaller file."""
    return environment_file.with_name(environment_file.name + ".runner")


def render_units(
    repository: Path,
    environment_file: Path,
    runner_environment_file: Path | None = None,
    *,
    backup_directory: Path | None = None,
) -> dict[str, bytes]:
    """Render fixed templates without shell expansion or untrusted systemd directives."""
    runner_file = runner_environment_file or default_runner_environment_file(environment_file)
    replacements = {
        "@RUNNER_ENVIRONMENT@": _template_path(runner_file),
        "@REPOSITORY@": _template_path(repository),
        "@ENVIRONMENT@": _template_path(environment_file),
        "@BACKUP_DIRECTORY@": _template_path(backup_directory or repository / "backups"),
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


def _backup_directory(repository: Path, configuration: bytes) -> Path:
    """Read the one sandbox path as a literal; never evaluate the private environment."""
    try:
        assignments = [
            line.partition("=")[2].strip()
            for line in configuration.decode("utf-8").splitlines()
            if line.partition("=")[0].strip() == "DECK_BACKUP_DIR"
        ]
    except UnicodeError:
        raise OperationError("invalid_backup_directory") from None
    if len(assignments) > 1:
        raise OperationError("invalid_backup_directory")
    value = assignments[0] if assignments else "backups"
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        value = value[1:-1]
    if not value or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None:
        raise OperationError("invalid_backup_directory")
    path = Path(value)
    if not path.is_absolute():
        path = repository / path
    _template_path(path)
    return path


def install_units(
    repository: Path,
    environment_file: Path,
    destination: Path,
    runner_environment_file: Path | None = None,
) -> None:
    """Create units without replacing existing user files, and never start services."""
    runner_file = runner_environment_file or default_runner_environment_file(environment_file)
    runtime_configuration = b""
    for configured in (environment_file, runner_file):
        with private_root(configured.parent) as configuration:
            content = read_file(configuration, configured.name, maximum=65536)
            if configured == environment_file:
                runtime_configuration = content
    backup_directory = _backup_directory(repository, runtime_configuration)
    rendered = render_units(
        repository, environment_file, runner_file, backup_directory=backup_directory
    )
    with private_root(destination, create=True) as directory:
        for name, expected in rendered.items():
            try:
                existing = read_file(directory, name, maximum=65536)
            except FileNotFoundError:
                continue
            if existing != expected:
                raise OperationError("unit_conflict")
        # ReadWritePaths requires the path to exist before systemd creates its sandbox.
        # Private descriptor traversal also rejects symlinks and overly broad access.
        with private_root(backup_directory, create=True):
            pass
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
    return {
        "PATH": operation_path(),
        **{key: os.environ[key] for key in allowed if key in os.environ},
    }


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


def _record_update(
    database_url: str, operation_id: UUID, phase: str, *, reason: str | None = None
) -> None:
    """Persist update phases separately from the subprocesses and their diagnostics."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="update",
                phase=phase,
                reason=reason,
            )
            session.commit()
    finally:
        engine.dispose()


def _recover_update(
    repository: Path, previous: str, active: tuple[str, ...], environment: dict[str, str]
) -> bool:
    """Attempt each recovery step even if another fails; never claim schema rollback."""
    root = str(repository.absolute())
    commands: list[tuple[str, ...]] = []
    if active:
        commands.append(("systemctl", "--user", "stop", *active))
    commands.extend(
        [
            ("git", "-C", root, "checkout", "--detach", previous),
            ("uv", "sync", "--frozen", "--project", root),
        ]
    )
    recovered = True
    try:
        for command in commands:
            try:
                run_command(command, env=environment, maximum_output=65536, cwd=repository)
            except Exception:
                recovered = False
    finally:
        # A failed checkout/sync/start must not hide the original update failure or
        # prevent an independent attempt to restore another previously active unit.
        for unit in active:
            try:
                run_command(
                    ("systemctl", "--user", "start", unit),
                    env=environment,
                    maximum_output=65536,
                    cwd=repository,
                )
            except Exception:
                recovered = False
    return recovered


def update_checkout(
    repository: Path, *, database_url: str, backup_root: Path, knowledge_url: str | None = None
) -> None:
    """Back up before mutation and restore the prior checkout/service state on failure."""
    operation_id = uuid4()
    previous: str | None = None
    active: tuple[str, ...] = ()
    mutation_started = False
    environment = service_environment()
    try:
        root = _template_path(repository)
        if _read_command(("git", "-C", root, "status", "--porcelain"), environment).strip():
            raise OperationError("dirty_checkout")
        if (
            _read_command(("git", "-C", root, "branch", "--show-current"), environment).strip()
            != b"main"
        ):
            raise OperationError("unsupported_update_branch")
        _record_update(database_url, operation_id, "started")
        create_backup(database_url, backup_root, knowledge_url=knowledge_url)
        previous = (
            _read_command(("git", "-C", root, "rev-parse", "--verify", "HEAD"), environment)
            .decode("ascii")
            .strip()
        )
        if re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", previous) is None:
            raise OperationError("invalid_update_revision")
        states = service_status()
        if any(states[unit] in {"activating", "deactivating", "reloading"} for unit in _SERVICES):
            raise OperationError("unstable_service_state")
        active = tuple(unit for unit in _SERVICES if states[unit] == "active")
        mutation_started = True
        if active:
            run_command(
                ("systemctl", "--user", "stop", *active), env=environment, maximum_output=65536
            )
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
        if active:
            run_command(
                ("systemctl", "--user", "start", *active), env=environment, maximum_output=65536
            )
        _record_update(database_url, operation_id, "completed")
    except Exception as error:
        recovered = True
        if mutation_started and previous is not None:
            recovered = _recover_update(repository, previous, active, environment)
        reason = "update_failed" if recovered else "update_rollback_failed"
        try:
            _record_update(database_url, operation_id, "failed", reason=reason)
        except Exception:
            # An unavailable audit database never turns a failed update into success.
            pass
        if not mutation_started and isinstance(error, OperationError):
            raise
        raise OperationError(reason) from None


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
    parser.add_argument(
        "--runner-environment-file",
        type=Path,
        help="Runner-only settings (default: <environment-file>.runner); no API secrets",
    )
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
                arguments.repository,
                arguments.environment_file,
                arguments.unit_directory,
                arguments.runner_environment_file,
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
                knowledge_url=configured_knowledge_url(for_tests=False),
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
        return 78 if arguments.action == "update" else 1


if __name__ == "__main__":
    raise SystemExit(main())
