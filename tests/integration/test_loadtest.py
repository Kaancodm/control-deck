"""End-to-end loadtest proof on the isolated test database (stage 18, D2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.config import get_test_database_url
from app.db.models import Event, Lease, Result, Task
from app.db.state_machine import TaskState

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "loadtest.py"


@pytest.mark.parametrize("worker", ["ollama", "claude"])
def test_loadtest_proves_queue_invariants(db_session: Session, worker: str) -> None:
    environment = {
        **os.environ,
        "DECK_TEST_DATABASE_URL": get_test_database_url(),
        "PYTHONPATH": str(_REPO_ROOT),
    }
    environment.pop("DECK_DATABASE_URL", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--tasks",
            "6",
            "--runners",
            "3",
            "--worker",
            worker,
            "--timeout-s",
            "120",
        ],
        capture_output=True,
        text=True,
        env=environment,
        cwd=_REPO_ROOT,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "pass"
    assert summary["failed_checks"] == []
    assert summary["tasks"] == 6
    assert summary["results"] == 6
    assert summary["acquire_events"] == 6
    assert summary["leases_remaining"] == 0
    assert summary["attempts_completed"] == 6
    assert summary["workers_reported"] == 3

    # Independent verification: the script must not grade its own homework only.
    db_session.expire_all()
    tasks = db_session.scalars(
        select(Task).where(Task.payload["labels"]["run"].astext == summary["run"])
    ).all()
    assert len(tasks) == 6
    assert all(task.status == TaskState.REVIEW for task in tasks)
    assert all(task.attempt == 1 for task in tasks)
    ids = [task.id for task in tasks]
    assert (
        db_session.scalar(select(func.count()).select_from(Result).where(Result.task_id.in_(ids)))
        == 6
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.task_id.in_(ids), Event.event_type == "task.acquire")
        )
        == 6
    )
    assert (
        db_session.scalar(select(func.count()).select_from(Lease).where(Lease.task_id.in_(ids)))
        == 0
    )
