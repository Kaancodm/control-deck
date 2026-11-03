"""Maintenance entrypoint: planning by default, explicit application and database target."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.config import get_database_url, get_test_database_url
from app.operations.backup import backup_summary, create_backup, prune_backups
from app.operations.errors import OperationError
from app.operations.restore import restore_backup
from app.operations.retention import retain_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or explicitly apply local maintenance")
    parser.add_argument("operation", choices=("backup", "retention", "restore-probe"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--target", choices=("test", "runtime"), default="test")
    parser.add_argument("--backup-name")
    parser.add_argument("--restic", action="store_true")
    arguments = parser.parse_args()
    if not arguments.apply:
        print(json.dumps({"operation": arguments.operation, "status": "dry_run"}))
        return 0
    try:
        root = Path(os.environ.get("DECK_BACKUP_DIR", "backups"))
        if arguments.operation == "restore-probe":
            if arguments.target != "test" or not arguments.backup_name:
                raise OperationError("unsafe_restore_target")
            operation_id = restore_backup(
                root,
                arguments.backup_name,
                target_url=os.environ.get("DECK_RESTORE_TEST_DATABASE_URL", ""),
                audit_url=get_test_database_url(),
            )
            print(json.dumps({"operation_id": str(operation_id), "status": "completed"}))
            return 0
        url = get_test_database_url() if arguments.target == "test" else get_database_url()
        if arguments.operation == "backup":
            result = create_backup(url, root)
            if arguments.restic:
                from app.operations.restic import copy_with_restic

                copy_with_restic(
                    url,
                    backup_root=root,
                    runs_root=Path(os.environ.get("DECK_RUNS_DIR", "runs")),
                    configuration=os.environ,
                )
            prune_backups(root, apply=True)
            print(backup_summary(result))
            return 0
        engine = create_engine(url, pool_pre_ping=True)
        try:
            result_summary = retain_tasks(
                sessionmaker(bind=engine, autoflush=False),
                Path(os.environ.get("DECK_RUNS_DIR", "runs")),
                apply=True,
            )
            print(json.dumps(asdict(result_summary)))
            return 1 if result_summary.failed else 0
        finally:
            engine.dispose()
    except Exception as error:
        reason = error.code if isinstance(error, OperationError) else "configuration_unavailable"
        print(json.dumps({"status": "failed", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
