"""Host orchestration closes transactions and commits normal stop decisions."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import ResultOutput, TaskV1, Worker
from app.db.models import Event, Lease, RedactionMap, Result, Task
from app.db.repository import approve_task, create_task_from_v1
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel
from app.workers.artifacts import WorkerArtifactError
from app.workers.contracts import AttemptContext
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_database import _minimal_task_payload


def _queued(session: Session, *, data_class: str = "local_only", text: str = "ordinary text"):
    payload = _minimal_task_payload(uuid4())
    payload["data_class"] = data_class
    payload["instructions"] = text
    payload["fallback_chain"] = ["ollama"] if data_class == "local_only" else ["claude"]
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    approve_task(session, task)
    task_id = task.id
    session.commit()
    return task_id


def _runner(session: Session, tmp_path: Path, *, factory=None):
    factory = factory or sessionmaker(bind=session.get_bind(), autoflush=False)
    return HostRunner(
        factory,
        runs_root=tmp_path / "runs",
        lease_ttl=timedelta(seconds=10),
        heartbeat_interval_s=0.03,
    ), factory


class CountingExecutor(SimulationExecutor):
    calls = 0

    def execute(self, context: AttemptContext):
        self.calls += 1
        return super().execute(context)


@pytest.mark.parametrize(
    ("has_lane", "lane"),
    [(False, None), (True, None), (True, "ollama"), (True, Worker.CLAUDE)],
    ids=["missing", "null", "untyped", "cloud-executor"],
)
def test_executor_identity_is_required_before_claim_or_payload_preparation(
    db_session: Session, tmp_path: Path, has_lane: bool, lane: object
) -> None:
    task_id = _queued(db_session, text="private local-only task text")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    opened_sessions = []

    def tracked_factory():
        session = factory()
        opened_sessions.append(session)
        return session

    class InvalidIdentityExecutor:
        calls = 0

        def execute(self, context):
            self.calls += 1
            raise AssertionError("An unidentified executor must never receive a payload")

    executor = InvalidIdentityExecutor()
    if has_lane:
        executor.lane = lane
    runner, _ = _runner(db_session, tmp_path, factory=tracked_factory)
    outcome = runner.run_once(Worker.OLLAMA, executor)

    assert outcome.action == "error"
    assert outcome.error_code == "executor_lane_mismatch"
    assert executor.calls == 0
    assert opened_sessions == []
    assert not (tmp_path / "runs").exists()
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.QUEUED
    assert task.attempt == 0
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "task.prepare")
        )
        == 0
    )


@pytest.mark.parametrize("boundary", ["claim_prepared_attempt", "start_worker_attempt"])
def test_executor_identity_is_rechecked_before_prepared_payload_delivery(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from app.workers import runner as runner_module

    task_id = _queued(db_session, text="private local-only task text")
    runner, _ = _runner(db_session, tmp_path)
    executor = CountingExecutor(Worker.OLLAMA)
    original = getattr(runner_module, boundary)

    def reconfigure_executor(*args, **kwargs):
        outcome = original(*args, **kwargs)
        executor.lane = Worker.CLAUDE
        return outcome

    monkeypatch.setattr(runner_module, boundary, reconfigure_executor)
    outcome = runner.run_once(Worker.OLLAMA, executor)

    assert outcome.action == "error"
    assert outcome.error_code == "executor_lane_mismatch"
    assert executor.calls == 0
    db_session.expire_all()
    expected = TaskState.LEASED if boundary == "claim_prepared_attempt" else TaskState.RUNNING
    assert db_session.get(Task, task_id).status == expected
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0


def test_host_success_has_no_open_runner_transaction_during_execution(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    sessions = []

    def tracked_factory():
        session = factory()
        sessions.append(session)
        return session

    runner, _ = _runner(db_session, tmp_path, factory=tracked_factory)

    class Inspecting(SimulationExecutor):
        def execute(self, context):
            assert all(not session.in_transaction() for session in sessions)
            assert not hasattr(context, "task")
            assert not hasattr(context, "mapping")
            assert (context.run_dir / "payload.sent.md").read_text() == context.payload_text
            with factory() as fresh:
                assert fresh.get(Task, task_id).status == TaskState.RUNNING
                assert (
                    fresh.scalar(
                        select(func.count())
                        .select_from(Event)
                        .where(Event.task_id == task_id, Event.event_type == "task.prepare")
                    )
                    == 1
                )
            return super().execute(context)

    outcome = runner.run_once(Worker.OLLAMA, Inspecting(Worker.OLLAMA))
    assert outcome.action == "completed"
    assert outcome.status == "review"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.REVIEW
    directory = tmp_path / "runs" / str(task_id) / "1"
    stored = db_session.scalar(select(Result.payload).where(Result.task_id == task_id))
    assert json.loads((directory / "result.json").read_bytes()) == stored
    assert (directory / "ollama.log").read_text() == "attempt=1 status=review\n"
    assert (directory / "out").is_dir()


def test_cloud_executor_receives_only_redacted_payload_and_result_is_local(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    literals = tmp_path / "terms.txt"
    literals.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(literals))
    original = "synthetic" + "@" + "example.invalid"
    task_id = _queued(db_session, data_class="cloud_redacted", text="Reply to " + original)
    runner, factory = _runner(db_session, tmp_path)

    class Echo(SimulationExecutor):
        def execute(self, context):
            assert original not in context.payload_text
            assert "[[CD_" in context.payload_text
            assert original not in repr(context)
            with factory() as session:
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(RedactionMap)
                        .where(RedactionMap.task_id == task_id)
                    )
                    == 1
                )
            result = super().execute(context)
            return result.model_copy(
                update={"output": ResultOutput(text=context.payload_text, files=[])}
            )

    outcome = runner.run_once(Worker.CLAUDE, Echo(Worker.CLAUDE))
    assert outcome.action == "completed"
    db_session.expire_all()
    result = db_session.scalar(select(Result.payload).where(Result.task_id == task_id))
    assert original in result["output"]["text"]
    sent = tmp_path / "runs" / str(task_id) / "1" / "payload.sent.md"
    assert original not in sent.read_text()


def test_paused_runner_does_not_claim_or_call_executor(db_session: Session, tmp_path: Path) -> None:
    task_id = _queued(db_session)
    update_settings(db_session, {"paused": True})
    db_session.commit()
    runner, _ = _runner(db_session, tmp_path)
    executor = CountingExecutor(Worker.OLLAMA)
    assert runner.run_once(Worker.OLLAMA, executor).action == "idle"
    assert executor.calls == 0
    db_session.expire_all()
    assert db_session.get(Task, task_id).attempt == 0


def test_owner_cancel_interrupts_cooperative_executor_and_is_acknowledged(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    runner, factory = _runner(db_session, tmp_path)

    class CancelWhileRunning(SimulationExecutor):
        def execute(self, context):
            with factory() as session:
                request_operator_cancel(session, task=session.get(Task, task_id), reason="test")
                session.commit()
            until = time.monotonic() + 2
            while not context.cancel_requested() and time.monotonic() < until:
                time.sleep(0.005)
            assert context.cancel_requested()
            return super().execute(context)

    assert runner.run_once(Worker.OLLAMA, CancelWhileRunning(Worker.OLLAMA)).action == "cancel"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.CANCELLED
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def test_deadline_stop_is_committed_before_executor_returns(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    runner, factory = _runner(db_session, tmp_path)

    class ExpireWhileRunning(SimulationExecutor):
        def execute(self, context):
            with factory() as session:
                session.get(Task, task_id).deadline_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()
            until = time.monotonic() + 2
            while not context.cancel_requested() and time.monotonic() < until:
                time.sleep(0.005)
            assert context.cancel_requested()
            with factory() as session:
                assert session.get(Task, task_id).status == TaskState.EXPIRED
            return super().execute(context)

    assert runner.run_once(Worker.OLLAMA, ExpireWhileRunning(Worker.OLLAMA)).action == "expired"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.EXPIRED
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0


def test_preparation_failure_never_calls_executor(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queued(db_session)
    runner, _ = _runner(db_session, tmp_path)
    executor = CountingExecutor(Worker.OLLAMA)

    def fail(*args, **kwargs):
        raise ValueError("raw content must not escape")

    monkeypatch.setattr("app.workers.runner.claim_prepared_attempt", fail)
    outcome = runner.run_once(Worker.OLLAMA, executor)
    assert outcome.error_code == "preparation_failed"
    assert "raw content" not in repr(outcome)
    assert executor.calls == 0


def test_output_artifact_failure_leaves_reapable_claim_and_never_executes(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _queued(db_session)
    runner, factory = _runner(db_session, tmp_path)
    executor = CountingExecutor(Worker.OLLAMA)

    def fail(*args, **kwargs):
        raise WorkerArtifactError("unsafe_directory")

    with monkeypatch.context() as patch:
        patch.setattr("app.workers.runner.initialize_attempt_output", fail)
        assert runner.run_once(Worker.OLLAMA, executor).error_code == "start_failed"
    assert executor.calls == 0
    with factory() as session:
        task = session.get(Task, task_id)
        assert task.status == TaskState.LEASED
        task.lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    outcome = runner.run_once(Worker.OLLAMA, executor)
    assert outcome.action == "completed"
    assert outcome.attempt == 2
    assert executor.calls == 1
    assert (tmp_path / "runs" / str(task_id) / "2" / "result.json").is_file()


def test_claim_commit_failure_never_exposes_prepared_text(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    count = 0

    class FailingCommit(Session):
        def commit(self):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("simulated commit failure")
            return super().commit()

    factory = sessionmaker(bind=db_session.get_bind(), class_=FailingCommit, autoflush=False)
    runner, _ = _runner(db_session, tmp_path, factory=factory)
    executor = CountingExecutor(Worker.OLLAMA)
    assert runner.run_once(Worker.OLLAMA, executor).error_code == "preparation_failed"
    assert executor.calls == 0
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.QUEUED
    assert task.attempt == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(Event).where(Event.event_type == "task.prepare")
        )
        == 0
    )


def test_database_failure_stops_cooperative_executor_without_accepting_result(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _queued(db_session)
    runner, _ = _runner(db_session, tmp_path)

    def unavailable(*args, **kwargs):
        raise RuntimeError("simulated DB unavailable")

    monkeypatch.setattr(runner, "_control", unavailable)

    class WaitForStop(SimulationExecutor):
        def execute(self, context):
            until = time.monotonic() + 2
            while not context.cancel_requested() and time.monotonic() < until:
                time.sleep(0.005)
            assert context.cancel_requested()
            return super().execute(context)

    assert runner.run_once(Worker.OLLAMA, WaitForStop(Worker.OLLAMA)).error_code == "control_failed"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.RUNNING
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0


def test_forged_worker_result_is_normalized_to_host_failure(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    runner, _ = _runner(db_session, tmp_path)

    class Forged(SimulationExecutor):
        def execute(self, context):
            return super().execute(context).model_copy(update={"worker": Worker.CLAUDE})

    outcome = runner.run_once(Worker.OLLAMA, Forged(Worker.OLLAMA))
    assert outcome.action == "completed"
    assert outcome.status == "failed"
    db_session.expire_all()
    result = db_session.scalar(select(Result.payload).where(Result.task_id == task_id))
    assert result["worker"] == "ollama"
    assert result["error"]["code"] == "internal"


def test_deadline_during_acceptance_commits_expiry(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import worker_ops

    task_id = _queued(db_session)
    runner, factory = _runner(db_session, tmp_path)
    original = worker_ops.evaluate_acceptance

    def cross_deadline(*args, **kwargs):
        with factory() as session:
            session.get(Task, task_id).deadline_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_ops, "evaluate_acceptance", cross_deadline)
    outcome = runner.run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
    assert outcome.action == "expired"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.EXPIRED
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0


def test_heartbeat_keeps_a_slow_cooperative_attempt_owned(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _queued(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    runner = HostRunner(
        factory,
        runs_root=tmp_path / "runs",
        lease_ttl=timedelta(seconds=0.3),
        heartbeat_interval_s=0.03,
    )

    class Slow(SimulationExecutor):
        def execute(self, context):
            until = time.monotonic() + 0.5
            while time.monotonic() < until:
                assert not context.cancel_requested()
                time.sleep(0.005)
            with factory() as session:
                lease = session.scalar(select(Lease).where(Lease.task_id == task_id))
                assert lease.expires_at > datetime.now(UTC)
                assert lease.heartbeat_at > lease.created_at
            return super().execute(context)

    outcome = runner.run_once(Worker.OLLAMA, Slow(Worker.OLLAMA))
    assert outcome.action == "completed"
    assert outcome.status == "review"


def test_attempt_timeout_cancels_cooperative_work_without_expiring_task(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from app.workers import runner as runner_module

    task_id = _queued(db_session)
    runner, _ = _runner(db_session, tmp_path)
    original = runner_module.claim_prepared_attempt

    def short_budget(*args, **kwargs):
        # Exercise the runner deadline without waiting for the schema's minimum
        # live timeout. The real task still has no owner-set expiry deadline.
        prepared = original(*args, **kwargs)
        return replace(prepared, timeout_s=1) if prepared is not None else None

    monkeypatch.setattr(runner_module, "claim_prepared_attempt", short_budget)

    class WaitForTimeout(SimulationExecutor):
        def execute(self, context):
            until = time.monotonic() + 2
            while not context.cancel_requested() and time.monotonic() < until:
                time.sleep(0.005)
            assert context.cancel_requested()
            return super().execute(context)

    outcome = runner.run_once(Worker.OLLAMA, WaitForTimeout(Worker.OLLAMA))
    assert outcome.action == "completed"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.FAILED
    assert db_session.get(Task, task_id).deadline_at is None
    result = db_session.scalar(select(Result.payload).where(Result.task_id == task_id))
    assert result["error"]["code"] == "timeout"
    assert result["output"]["text"] == ""
