"""Optional explicit off-site hook; credentials remain outside repository and logs."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.operations.audit import record_operation
from app.operations.backup import verified_sets
from app.operations.environment import operation_path
from app.operations.errors import OperationError
from app.operations.files import READ_FLAGS, private_root, validate_private
from app.operations.process import run_command


def copy_with_restic(
    url: str,
    *,
    backup_root: Path,
    runs_root: Path,
    configuration: Mapping[str, str],
    command: tuple[str, ...] = ("restic",),
) -> None:
    """Invoke one configured hook without inheriting general process credentials."""
    repository = configuration.get("RESTIC_REPOSITORY", "")
    password_path = configuration.get("RESTIC_PASSWORD_FILE", "")
    if not repository or not password_path or "\x00" in repository:
        raise OperationError("restic_configuration_required")
    credential = Path(password_path)
    operation_id = uuid4()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="backup",
                phase="started",
                reason="restic_copy",
            )
            session.commit()
        with (
            private_root(backup_root) as backups,
            private_root(runs_root),
            private_root(credential.parent) as parent,
            tempfile.TemporaryDirectory(prefix="deck-restic-") as cache,
        ):
            if not verified_sets(backups):
                raise OperationError("complete_backup_required")
            descriptor = os.open(credential.name, READ_FLAGS, dir_fd=parent)
            try:
                metadata = os.fstat(descriptor)
                validate_private(metadata, directory=False)
                if not 1 <= metadata.st_size <= 4096:
                    raise OperationError("unsafe_credential_file")
                environment = {
                    "PATH": operation_path(configuration),
                    "RESTIC_CACHE_DIR": cache,
                    "RESTIC_REPOSITORY": repository,
                    "RESTIC_PASSWORD_FILE": f"/proc/self/fd/{descriptor}",
                }
                run_command(
                    (
                        *command,
                        "backup",
                        "--",
                        str(backup_root.absolute()),
                        str(runs_root.absolute()),
                    ),
                    env=environment,
                    pass_fds=(descriptor,),
                    timeout_s=600,
                    maximum_output=65536,
                )
            finally:
                os.close(descriptor)
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="backup",
                phase="completed",
                reason="restic_copy",
            )
            session.commit()
    except Exception as error:
        try:
            with Session(engine) as session:
                record_operation(
                    session,
                    operation_id=operation_id,
                    operation_type="backup",
                    phase="failed",
                    reason="restic_failed",
                )
                session.commit()
        except Exception:
            pass
        if isinstance(error, OperationError):
            raise
        raise OperationError("restic_failed") from None
    finally:
        engine.dispose()
