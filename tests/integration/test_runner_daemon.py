"""Real supervisor shutdown leaves a recoverable lease, not an owner cancellation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.core.schemas import Worker
from app.db.config import get_test_database_url
from app.db.models import Event, LaneStatus, Lease, Result, Task
from app.db.state_machine import TaskState
from app.workers.daemon import run_loop
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_host_runner import _queued, _runner
from tests.support.ollama_stub import OllamaStub


def test_real_service_stop_terminates_process_group_and_recovers_lease(db_session, tmp_path):
    task_id = _queued(db_session)
    ready = tmp_path / "ready.json"
    child = """
import json, os, subprocess, sys, time
from pathlib import Path
from app.core.schemas import Worker
from app.db.session import get_session_factory
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
class Waiting(SimulationExecutor):
    def execute(self, context):
        descendant = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        Path(sys.argv[1]).write_text(json.dumps([os.getpid(), descendant.pid]))
        time.sleep(60)
        return super().execute(context)
HostRunner(get_session_factory(for_tests=True), runs_root=Path(sys.argv[2])).run_once(
    Worker.OLLAMA, Waiting(Worker.OLLAMA))
"""
    stop = threading.Event()
    outcomes = []

    def supervise():
        outcomes.append(
            run_loop(
                Worker.OLLAMA,
                environment={"PATH": os.defpath, "DECK_TEST_DATABASE_URL": get_test_database_url()},
                stop=stop,
                iterations=1,
                attempt_timeout_s=30,
                command=(sys.executable, "-c", child, str(ready), str(tmp_path / "runs")),
            )
        )

    supervisor = threading.Thread(target=supervise)
    supervisor.start()
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline and supervisor.is_alive():
            time.sleep(0.02)
        assert ready.exists(), "The real child must claim and begin its simulated attempt"
        pids = json.loads(ready.read_text())
    finally:
        stop.set()
        supervisor.join(timeout=10)
    assert not supervisor.is_alive()
    assert outcomes == [0]
    for pid in pids:
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.RUNNING
    assert not task.cancel_requested and task.attempt == 1
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    lease = db_session.scalar(select(Lease).where(Lease.task_id == task_id))
    assert lease is not None
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    runner, _ = _runner(db_session, tmp_path)
    outcome = runner.run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
    assert outcome.action == "completed"
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.REVIEW and task.attempt == 2
    assert not task.cancel_requested
    rows = list(db_session.scalars(select(Result).order_by(Result.attempt)))
    assert [row.attempt for row in rows] == [1, 2]
    assert rows[0].execution_profile == "unknown"
    assert rows[0].payload["error"]["code"] == "worker_unavailable"
    assert rows[1].payload["status"] == "succeeded"
    assert not any("cancel" in kind for kind in db_session.scalars(select(Event.event_type)))


def test_live_daemon_with_unreachable_stub_keeps_polling_and_stops_cleanly(db_session, tmp_path):
    task_id = _queued(db_session)
    router = tmp_path / "router.yaml"
    router.write_text(
        json.dumps(
            {
                "version": 1,
                "lanes": {"ollama": {"enabled": True, "models": {"default": "fixture:latest"}}},
            }
        )
    )
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("")
    with OllamaStub() as stub:
        url = stub.url
    process = subprocess.Popen(
        [sys.executable, "-m", "app.workers.daemon", "--worker", "ollama"],
        env={
            "PATH": os.defpath,
            "DECK_DATABASE_URL": get_test_database_url(),
            "DECK_RUNS_DIR": str(tmp_path / "runs"),
            "DECK_ROUTER_FILE": str(router),
            "DECK_BLOCKLIST_PATH": str(blocklist),
            "DECK_LIVE_ENABLED": "1",
            "DECK_OLLAMA_URL": url,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            db_session.expire_all()
            health = db_session.get(LaneStatus, Worker.OLLAMA.value)
            if health is not None and health.status == "unavailable":
                break
            assert process.poll() is None, "Connection failure must not stop the daemon"
            time.sleep(0.02)
        else:
            raise AssertionError("The live runner did not persist unavailable health")
        assert process.poll() is None
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert json.loads(stdout)["status"] == "stopped"
        assert not stderr
        db_session.expire_all()
        task = db_session.get(Task, task_id)
        assert task.status == TaskState.QUEUED and task.attempt == 0
        assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
