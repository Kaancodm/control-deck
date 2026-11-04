"""The stage-18 harness uses real independent runners and a local disposable test DB."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.config import get_test_database_url
from app.db.models import Event, LaneStatus, Lease, Result, Setting, Task
from app.db.state_machine import TaskState
from scripts import loadtest


def test_loadtest_refuses_existing_work_without_spawning_or_modifying_it(db_session, monkeypatch):
    payload = loadtest.synthetic_task(uuid4(), uuid4())
    sentinel = Task(
        id=payload.task_id,
        payload=payload.model_dump(mode="json"),
        data_class=payload.data_class,
        status=TaskState.AWAITING_APPROVAL,
    )
    db_session.add(sentinel)
    db_session.commit()
    monkeypatch.setattr(
        loadtest, "_run_workers", lambda *args: pytest.fail("Existing work must not be claimed")
    )
    with pytest.raises(loadtest.LoadtestError, match="^test_database_not_empty$"):
        loadtest.run_loadtest(loadtest.Options(tasks=1, runners=1))
    db_session.expire_all()
    assert db_session.get(Task, payload.task_id).status == TaskState.AWAITING_APPROVAL
    for model in (Result, Event, Lease, Setting, LaneStatus):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0


def test_loadtest_exclusive_lock_refuses_a_second_harness(db_session):
    db_session.execute(select(func.pg_advisory_lock(loadtest._LOCK)))
    try:
        with pytest.raises(loadtest.LoadtestError, match="^loadtest_already_running$"):
            loadtest.run_loadtest(loadtest.Options(tasks=1, runners=1))
        assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    finally:
        db_session.execute(select(func.pg_advisory_unlock(loadtest._LOCK)))


def test_loadtest_rejects_a_database_not_at_head_before_writes(db_session):
    db_session.execute(loadtest.text("UPDATE alembic_version SET version_num = 'not_head'"))
    with pytest.raises(loadtest.LoadtestError, match="^test_database_not_at_head$"):
        loadtest._check_database(db_session, get_test_database_url())
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_loadtest_timeout_is_nonzero_and_joins_started_runner_processes(db_session, capsys):
    existing = {process.pid for process in loadtest.multiprocessing.active_children()}
    assert loadtest.main(["--tasks", "1", "--runners", "1", "--timeout", "0.001"]) == 1
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "FAIL"
    assert "loadtest_timeout" in report["violations"]
    assert {process.pid for process in loadtest.multiprocessing.active_children()} == existing
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def test_stage18_fifty_tasks_three_real_fake_cli_runner_processes(
    db_session, monkeypatch, tmp_path, capsys
):
    # A stale live setting must not select a real provider in any child process.
    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    monkeypatch.setenv("DECK_DATABASE_URL", "unrelated-runtime-setting")
    destination = tmp_path / "loadtest.json"
    assert loadtest.main(["--json-output", str(destination)]) == 0
    report = json.loads(destination.read_text())
    assert report == json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "PASS"
    assert report["tasks"] == report["completed_tasks"] == 50
    assert report["runners"] == 3
    workers = report["runner_processes"]
    assert len({worker["pid"] for worker in workers}) == 3
    assert all(worker["pid"] != os.getpid() and worker["exit_code"] == 0 for worker in workers)
    assert all(worker["completed"] > 0 for worker in workers)
    assert sum(worker["completed"] for worker in workers) == 50
    assert not any(report["counters"].values())
    assert report["error_rate"] == 0
    assert report["latency_seconds"]["samples"] == 50
    assert 0 <= report["latency_seconds"]["p50"] <= report["latency_seconds"]["p95"]
    assert report["throughput_tasks_per_second"] > 0
    db_session.expire_all()
    tasks = list(db_session.scalars(select(Task)))
    expected = {(task.id, 1) for task in tasks}
    assert len(expected) == 50
    assert all(task.status == TaskState.REVIEW and task.attempt == 1 for task in tasks)
    assert all(task.approved_at is not None for task in tasks)
    assert all(task.payload["labels"]["loadtest_run"] == report["run_id"] for task in tasks)
    result_counts = db_session.execute(
        select(Result.task_id, Result.attempt, func.count()).group_by(
            Result.task_id, Result.attempt
        )
    ).all()
    acquire_counts = db_session.execute(
        select(Event.task_id, Event.attempt, func.count())
        .where(Event.event_type == "task.acquire")
        .group_by(Event.task_id, Event.attempt)
    ).all()
    assert {
        (task_id, attempt) for task_id, attempt, count in result_counts if count == 1
    } == expected
    assert {
        (task_id, attempt) for task_id, attempt, count in acquire_counts if count == 1
    } == expected
    assert set(db_session.scalars(select(Result.execution_profile))) == {"simulation"}
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
