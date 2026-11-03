"""Stopping a supervisor interrupts execution without impersonating owner cancellation."""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import Worker
from app.db.config import get_test_database_url
from app.db.models import Lease, Result, Task
from app.db.queue import reap_expired_leases
from app.db.state_machine import TaskState
from app.workers.daemon import run_loop
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_host_runner import _queued

_CHILD = """
import os
import time
from datetime import timedelta
from pathlib import Path
from app.core.schemas import Worker
from app.db.session import get_session_factory
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor

class WaitingExecutor(SimulationExecutor):
    def execute(self, context):
        while not context.cancel_requested():
            time.sleep(0.01)
        return super().execute(context)

runner = HostRunner(get_session_factory(), runs_root=Path(os.environ['DECK_RUNS_DIR']),
                    lease_ttl=timedelta(seconds=2), heartbeat_interval_s=0.05)
runner.run_once(Worker.OLLAMA, WaitingExecutor(Worker.OLLAMA))
"""


def test_service_stop_preserves_lease_retry_instead_of_owner_cancel(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    runs = tmp_path / "runs"
    stop = threading.Event()
    outcomes = []
    environment = {
        "PATH": os.defpath,
        "DECK_DATABASE_URL": get_test_database_url(),
        "DECK_RUNS_DIR": str(runs),
    }

    def supervise():
        outcomes.append(
            run_loop(
                Worker.OLLAMA,
                environment=environment,
                stop=stop,
                iterations=1,
                attempt_timeout_s=15,
                command=(sys.executable, "-c", _CHILD),
            )
        )

    thread = threading.Thread(target=supervise)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            db_session.expire_all()
            if db_session.get(Task, task_id).status == TaskState.RUNNING:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("The simulated child did not begin its task")
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        db_session.expire_all()
        task = db_session.get(Task, task_id)
        assert task.status == TaskState.RUNNING
        assert task.cancel_requested is False
        assert db_session.scalar(select(func.count()).select_from(Result)) == 0
        lease = db_session.scalar(select(Lease).where(Lease.task_id == task_id))
        assert lease is not None
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
        reap_expired_leases(db_session)
        db_session.commit()
        assert db_session.get(Task, task_id).status == TaskState.QUEUED
        factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
        outcome = HostRunner(factory, runs_root=runs).run_once(
            Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA)
        )
        assert outcome.action == "completed" and outcome.attempt == 2
        db_session.expire_all()
        assert db_session.get(Task, task_id).status == TaskState.REVIEW
    finally:
        stop.set()
        thread.join(timeout=5)
