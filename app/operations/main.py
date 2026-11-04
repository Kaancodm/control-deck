"""Maintenance entrypoint: planning by default, explicit application and database target."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import get_database_url, get_test_database_url
from app.operations.audit import record_operation
from app.operations.backup import (
    backup_summary,
    configured_knowledge_url,
    create_backup,
    prune_backups,
)
from app.operations.errors import OperationError
from app.operations.restore import restore_backup
from app.operations.retention import retain_tasks


def backup_root(target: str) -> Path:
    """Test runs get their own directory; they never write to or prune the production root."""
    if target == "test":
        return Path(os.environ.get("DECK_TEST_BACKUP_DIR", "backups_test"))
    return Path(os.environ.get("DECK_BACKUP_DIR", "backups"))


def _record_backup_retention_issue(url: str, operation_id: UUID, reason: str) -> None:
    """Persist bounded retention diagnostics independently of the completed backup."""
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            record_operation(
                session,
                operation_id=operation_id,
                operation_type="retention",
                phase="failed",
                reason=reason,
                counts={"backups": 1} if reason == "invalid_backup_set" else None,
            )
            session.commit()
    finally:
        engine.dispose()


def _prune_after_backup(url: str, root: Path) -> str:
    """Report retention failures without turning a completed backup into a failed dump."""
    invalid_found = False
    audit_failed = False

    def report_invalid(operation_id: UUID) -> None:
        nonlocal invalid_found, audit_failed
        invalid_found = True
        try:
            _record_backup_retention_issue(url, operation_id, "invalid_backup_set")
        except Exception:
            audit_failed = True
            raise

    try:
        prune_backups(root, apply=True, on_invalid=report_invalid)
    except Exception:
        if audit_failed:
            return "audit_failed"
        try:
            _record_backup_retention_issue(url, uuid4(), "backup_retention_failed")
        except Exception:
            return "audit_failed"
        return "failed"
    return "partial_failure" if invalid_found else "completed"


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
        if arguments.operation == "restore-probe":
            if arguments.target != "test" or not arguments.backup_name:
                raise OperationError("unsafe_restore_target")
            # Probes only read a published set; they never write to or prune the directory.
            operation_id = restore_backup(
                backup_root("runtime"),
                arguments.backup_name,
                target_url=os.environ.get("DECK_RESTORE_TEST_DATABASE_URL", ""),
                audit_url=get_test_database_url(),
                knowledge_target_url=os.environ.get("DECK_KB_RESTORE_TEST_DATABASE_URL", ""),
                knowledge_source_url=configured_knowledge_url(for_tests=True),
            )
            print(json.dumps({"operation_id": str(operation_id), "status": "completed"}))
            return 0
        url = get_test_database_url() if arguments.target == "test" else get_database_url()
        if arguments.operation == "backup":
            root = backup_root(arguments.target)
            result = create_backup(
                url,
                root,
                knowledge_url=configured_knowledge_url(for_tests=arguments.target == "test"),
            )
            if arguments.restic:
                from app.operations.restic import copy_with_restic

                copy_with_restic(
                    url,
                    backup_root=root,
                    runs_root=Path(os.environ.get("DECK_RUNS_DIR", "runs")),
                    configuration=os.environ,
                )
            # Retention runs only on the directory this target wrote to.
            summary = json.loads(backup_summary(result))
            summary["retention"] = _prune_after_backup(url, root)
            print(json.dumps(summary))
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
