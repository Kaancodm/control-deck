"""Offline CLI execution preserves approval, preparation, control and acceptance boundaries."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, LaneStatus, Lease, RedactionMap, Result, Task
from app.db.queue import dequeue_next_queued_task
from app.db.repository import approve_task, create_task_from_v1
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel
from app.workers.lane_status import LaneHealthState, get_lane_status, update_lane_status
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.ollama_lane import OllamaLaneRunner
from tests.integration.test_database import _minimal_task_payload


def _write_config(path: Path, *, models: dict[str, str], enabled: bool = True) -> None:
    path.write_text(
        json.dumps({"version": 1, "lanes": {"ollama": {"enabled": enabled, "models": models}}})
    )


@pytest.fixture(autouse=True)
def router_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "router.yaml"
    _write_config(path, models={"default": "fixture-model"})
    monkeypatch.setenv("DECK_ROUTER_FILE", str(path))
    literals = tmp_path / "terms.txt"
    literals.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(literals))
    return path


def _task(
    session: Session,
    *,
    data_class: str = "local_only",
    text: str = "ordinary task text",
    acceptance: list[dict[str, str]] | None = None,
    approved: bool = True,
    kind: str = "custom",
) -> UUID:
    payload = _minimal_task_payload(uuid4())
    payload["data_class"] = data_class
    payload["instructions"] = text
    payload["kind"] = kind
    payload["acceptance"] = acceptance or []
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    if approved:
        approve_task(session, task)
    task_id = task.id
    session.commit()
    return task_id


class RecordingExecutor(OllamaExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls: list[tuple[str, ...]] = []
        self.run_payloads: list[bytes] = []

    def _call(self, arguments: Sequence[str], **kwargs):
        self.calls.append(tuple(arguments))
        if arguments[0] == "run":
            self.run_payloads.append(kwargs["payload"])
        return super()._call(arguments, **kwargs)

    @property
    def run_calls(self) -> list[tuple[str, ...]]:
        return [arguments for arguments in self.calls if arguments[0] == "run"]


def _executor(*, behavior: str = "ok", **kwargs) -> RecordingExecutor:
    return RecordingExecutor(
        profile="simulation",
        simulation=OllamaSimulation(models=("fixture-model",), behavior=behavior),
        **kwargs,
    )


def _runner(session: Session, tmp_path: Path, executor: OllamaExecutor, *, factory=None):
    factory = factory or sessionmaker(bind=session.get_bind(), autoflush=False)
    return OllamaLaneRunner(
        factory,
        runs_root=tmp_path / "runs",
        executor=executor,
        lease_ttl=timedelta(seconds=10),
        heartbeat_interval_s=0.03,
    )


def _stored(session: Session, task_id: UUID):
    session.expire_all()
    return session.scalar(select(Result.payload).where(Result.task_id == task_id))


def _assert_unclaimed(session: Session, task_id: UUID) -> None:
    session.expire_all()
    task = session.get(Task, task_id)
    assert task is not None
    assert task.status == TaskState.QUEUED
    assert task.attempt == 0
    assert session.scalar(select(func.count()).select_from(Lease)) == 0
    assert session.scalar(select(func.count()).select_from(Result)) == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.task_id == task_id, Event.event_type == "task.acquire")
        )
        == 0
    )


def test_simulated_cli_runs_only_after_owner_approval_and_reaches_review(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(
        db_session,
        approved=False,
        acceptance=[{"type": "must_contain", "value": "simulation-ok"}],
    )
    executor = _executor()
    runner = _runner(db_session, tmp_path, executor)
    assert runner.run_once().action == "idle"
    assert executor.run_calls == []
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.AWAITING_APPROVAL
    assert task.attempt == 0
    approve_task(db_session, task)
    db_session.commit()

    outcome = runner.run_once()

    assert outcome.action == "completed"
    assert outcome.status == "review"
    assert outcome.task_id == task_id
    assert outcome.attempt == 1
    assert executor.run_calls == [("run", "fixture-model")]
    stored = _stored(db_session, task_id)
    assert stored["status"] == "succeeded"
    assert stored["worker"] == "ollama"
    assert stored["checks"] == [
        {"type": "must_contain", "passed": True, "detail": "Acceptance rule passed."},
        {"type": "execution_profile", "passed": False, "detail": "simulation"},
    ]
    assert db_session.get(Task, task_id).status == TaskState.REVIEW
    events = list(
        db_session.scalars(
            select(Event.event_type).where(Event.task_id == task_id).order_by(Event.id)
        )
    )
    lifecycle = [
        "task.submit",
        "task.approve",
        "task.acquire",
        "task.prepare",
        "task.start",
        "task.succeed",
    ]
    assert [event for event in events if event in lifecycle] == lifecycle
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "ok"
    assert health.reason == "simulation_ready"
    assert health.last_success_at is not None


def test_echo_uses_exact_archived_payload_bytes_and_hash(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(db_session, text="Preserve Unicode: Grüezi.\nKeep this next line.")
    executor = _executor(behavior="echo")

    assert _runner(db_session, tmp_path, executor).run_once().status == "review"

    directory = tmp_path / "runs" / str(task_id) / "1"
    sent = (directory / "payload.sent.md").read_bytes()
    stored = _stored(db_session, task_id)
    assert executor.run_payloads == [sent]
    assert stored["output"]["text"].encode("utf-8") == sent
    assert stored["payload_hash"] == hashlib.sha256(sent).hexdigest()
    assert json.loads((directory / "result.json").read_bytes()) == stored
    prepare = db_session.scalar(
        select(Event.detail).where(Event.task_id == task_id, Event.event_type == "task.prepare")
    )
    assert prepare["payload_sha256"] == stored["payload_hash"]
    log = json.loads((directory / "ollama.log").read_text())
    assert log["attempt"] == 1 and log["status"] == "review"
    assert log["execution_profile"] == "simulation"


def test_cloud_redacted_ollama_echo_is_rehydrated_only_locally(
    db_session: Session, tmp_path: Path
) -> None:
    original = "synthetic" + "@" + "example.invalid"
    task_id = _task(db_session, data_class="cloud_redacted", text="Reply to " + original)
    executor = _executor(behavior="echo")

    assert _runner(db_session, tmp_path, executor).run_once().status == "review"

    sent = executor.run_payloads[0].decode()
    assert original not in sent
    assert "[[CD_" in sent
    stored = _stored(db_session, task_id)
    assert original in stored["output"]["text"]
    assert "[[CD_" not in stored["output"]["text"]
    assert stored["redaction"] == {"applied": True, "placeholders": 1}
    assert (
        db_session.scalar(
            select(func.count()).select_from(RedactionMap).where(RedactionMap.task_id == task_id)
        )
        == 1
    )


def test_availability_and_execution_close_main_thread_database_transactions(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    sessions: list[tuple[int, Session]] = []
    inspected: list[str] = []

    def tracked_factory():
        session = factory()
        sessions.append((threading.get_ident(), session))
        return session

    class Inspecting(RecordingExecutor):
        def _call(self, arguments, **kwargs):
            # The monitor may independently heartbeat; the execution thread holds no session.
            assert all(
                not session.in_transaction()
                for thread_id, session in sessions
                if thread_id == threading.get_ident()
            )
            inspected.append(arguments[0])
            if arguments[0] == "run":
                with factory() as fresh:
                    task = fresh.get(Task, task_id)
                    assert task.status == TaskState.RUNNING
                    assert task.approved_at is not None
                    assert (
                        fresh.scalar(
                            select(func.count())
                            .select_from(Event)
                            .where(Event.task_id == task_id, Event.event_type == "task.start")
                        )
                        == 1
                    )
            return super()._call(arguments, **kwargs)

    executor = Inspecting(
        profile="simulation", simulation=OllamaSimulation(models=("fixture-model",))
    )
    outcome = _runner(db_session, tmp_path, executor, factory=tracked_factory).run_once()
    assert outcome.status == "review"
    assert "list" in inspected
    assert inspected.count("run") == 1


@pytest.mark.parametrize(
    ("models", "available", "reason"),
    [
        ({}, ("fixture-model",), "model_not_configured"),
        ({"default": "fixture-model"}, (), "model_not_available"),
        (
            {"default": "fixture-model", "summarize": "fixture-second"},
            ("fixture-model",),
            "model_not_available",
        ),
    ],
)
def test_unavailable_inventory_does_not_claim_or_consume_attempts(
    db_session: Session, tmp_path: Path, router_file: Path, models, available, reason
) -> None:
    task_id = _task(db_session)
    _write_config(router_file, models=models)
    executor = RecordingExecutor(
        profile="simulation", simulation=OllamaSimulation(models=available)
    )

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "unavailable"
    assert outcome.error_code == reason
    assert executor.run_calls == []
    _assert_unclaimed(db_session, task_id)
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "unavailable"
    assert health.reason == reason
    assert health.last_success_at is None


def _assert_blocked_for_missing_model(session: Session, task_id: UUID, *, kind: str) -> None:
    session.expire_all()
    task = session.get(Task, task_id)
    assert task is not None
    assert task.status == TaskState.BLOCKED
    assert task.attempt == 0
    assert (
        session.scalar(select(func.count()).select_from(Lease).where(Lease.task_id == task_id)) == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(Result).where(Result.task_id == task_id))
        == 0
    )
    events = list(
        session.scalars(
            select(Event).where(Event.task_id == task_id, Event.event_type == "task.block")
        )
    )
    assert len(events) == 1
    detail = events[0].detail
    assert events[0].from_status == TaskState.QUEUED
    assert events[0].to_status == TaskState.BLOCKED
    assert detail["reason"] == "routing"
    assert detail["code"] == "model_not_configured"
    assert detail["lane"] == "ollama"
    assert detail["kind"] == kind
    assert detail["attempt"] == 0
    assert "ordinary task text" not in json.dumps(detail)
    health = get_lane_status(session, lane=Worker.OLLAMA)
    assert health is not None
    assert health.status == "unavailable"
    assert health.reason == "model_not_configured"


def test_missing_model_for_task_kind_is_blocked_with_reason_and_lane_status(
    db_session: Session, tmp_path: Path, router_file: Path
) -> None:
    task_id = _task(db_session)
    _write_config(router_file, models={"summarize": "fixture-model"})
    executor = _executor()

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "idle"
    assert executor.run_calls == []
    _assert_blocked_for_missing_model(db_session, task_id, kind="custom")
    # The inventory probe itself succeeded; only the configuration gap is reported.
    assert get_lane_status(db_session, lane=Worker.OLLAMA).last_success_at is not None


def test_unconfigured_kind_is_blocked_while_configured_kind_still_runs(
    db_session: Session, tmp_path: Path, router_file: Path
) -> None:
    blocked_id = _task(db_session, kind="custom")
    runnable_id = _task(db_session, kind="summarize")
    _write_config(router_file, models={"summarize": "fixture-model"})
    executor = _executor()
    runner = _runner(db_session, tmp_path, executor)

    first = runner.run_once()

    assert first.action == "completed"
    assert first.task_id == runnable_id
    assert first.status == "review"
    assert executor.run_calls == [("run", "fixture-model")]
    _assert_blocked_for_missing_model(db_session, blocked_id, kind="custom")
    assert db_session.get(Task, runnable_id).status == TaskState.REVIEW

    second = runner.run_once()

    # Nothing misconfigured remains queued, so the next poll observes a healthy lane.
    assert second.action == "idle"
    assert executor.run_calls == [("run", "fixture-model")]
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "ok"
    assert health.reason == "simulation_ready"


def test_queue_separates_transient_inventory_gap_from_missing_model_configuration(
    db_session: Session, router_file: Path
) -> None:
    task_id = _task(db_session)
    update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    db_session.commit()

    # A configured model missing from the observed inventory is transient: no claim, no event.
    claimed = dequeue_next_queued_task(
        db_session,
        worker=Worker.OLLAMA,
        lease_ttl=timedelta(seconds=10),
        available_models=frozenset(),
    )
    db_session.commit()

    assert claimed is None
    _assert_unclaimed(db_session, task_id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.task_id == task_id, Event.event_type == "task.block")
        )
        == 0
    )
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "ok"
    assert health.reason == "simulation_ready"

    # No model for this task kind at all is permanent: blocked with an audit event.
    _write_config(router_file, models={"summarize": "fixture-model"})
    claimed = dequeue_next_queued_task(
        db_session,
        worker=Worker.OLLAMA,
        lease_ttl=timedelta(seconds=10),
        available_models=frozenset({"fixture-model"}),
    )
    db_session.commit()

    assert claimed is None
    _assert_blocked_for_missing_model(db_session, task_id, kind="custom")


def test_lane_without_inventory_does_not_block_unconfigured_kind(
    db_session: Session, router_file: Path
) -> None:
    # Only inventory-backed lanes treat a missing model as misconfiguration; a claim
    # without an observed inventory keeps the pre-existing executor-decides behaviour.
    task_id = _task(db_session)
    _write_config(router_file, models={"summarize": "fixture-model"})

    claimed = dequeue_next_queued_task(
        db_session, worker=Worker.OLLAMA, lease_ttl=timedelta(seconds=10)
    )

    assert claimed is not None
    assert claimed.id == task_id
    assert claimed.status == TaskState.LEASED
    assert claimed.plan["models"] == {"ollama": None}
    assert get_lane_status(db_session, lane=Worker.OLLAMA) is None


def test_changed_model_after_preflight_is_not_claimed(
    db_session: Session, tmp_path: Path, router_file: Path
) -> None:
    task_id = _task(db_session)

    class Reconfigured(RecordingExecutor):
        def availability(self, models):
            observed = super().availability(models)
            _write_config(router_file, models={"default": "fixture-changed"})
            return observed

    executor = Reconfigured(
        profile="simulation", simulation=OllamaSimulation(models=("fixture-model",))
    )
    assert _runner(db_session, tmp_path, executor).run_once().action == "idle"
    assert executor.run_calls == []
    _assert_unclaimed(db_session, task_id)


@pytest.mark.parametrize("setting", ["paused", "stop_requested", "lanes.ollama.paused"])
def test_owner_pause_or_stop_skips_inventory_and_claim(
    db_session: Session, tmp_path: Path, setting: str
) -> None:
    task_id = _task(db_session)
    update_settings(db_session, {setting: True})
    db_session.commit()
    executor = _executor()

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "idle"
    assert outcome.error_code == "lane_paused"
    assert executor.calls == []
    _assert_unclaimed(db_session, task_id)
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "paused"
    assert health.reason == "runner_paused"


def test_disabled_lane_skips_inventory_and_claim(
    db_session: Session, tmp_path: Path, router_file: Path
) -> None:
    task_id = _task(db_session)
    _write_config(router_file, models={"default": "fixture-model"}, enabled=False)
    executor = _executor()

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "idle"
    assert outcome.error_code == "lane_disabled"
    assert executor.calls == []
    _assert_unclaimed(db_session, task_id)
    assert get_lane_status(db_session, lane=Worker.OLLAMA).reason == "lane_disabled"


def test_default_live_profile_stays_unavailable_without_starting_any_process(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _task(db_session)
    processes = []

    def forbidden_process(*args, **kwargs):
        processes.append(args)
        raise AssertionError("A disabled live profile must not create a process")

    monkeypatch.setattr("app.workers.lanes.ollama.run_process", forbidden_process)
    outcome = _runner(db_session, tmp_path, OllamaExecutor()).run_once()

    assert outcome.action == "unavailable"
    assert outcome.error_code == "live_profile_unavailable"
    assert processes == []
    _assert_unclaimed(db_session, task_id)


def test_core_acceptance_failure_ends_in_review_without_hidden_execution(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(
        db_session, acceptance=[{"type": "must_contain", "value": "required missing phrase"}]
    )
    executor = _executor()

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "completed"
    assert outcome.status == "review"
    assert executor.run_calls == [("run", "fixture-model")]
    stored = _stored(db_session, task_id)
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "acceptance_failed"
    assert stored["checks"][0]["passed"] is False
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.REVIEW and task.attempt == 1
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.task_id == task_id, Event.event_type == "task.retry")
        )
        == 0
    )
    reviews = list(
        db_session.scalars(
            select(Event.detail).where(Event.task_id == task_id, Event.event_type == "task.review")
        )
    )
    assert len(reviews) == 1
    assert reviews[0]["reason"] == "acceptance_failed"


@pytest.mark.parametrize(
    ("behavior", "code"),
    [
        ("fail", "worker_unavailable"),
        ("empty", "acceptance_failed"),
        ("incomplete_zero", "acceptance_failed"),
        ("invalid_utf8", "internal"),
        ("stdout_limit", "internal"),
        ("stderr_limit", "internal"),
    ],
)
def test_simulated_failures_persist_safe_errors_without_hidden_retries(
    db_session: Session, tmp_path: Path, behavior: str, code: str
) -> None:
    task_id = _task(db_session)
    executor = _executor(behavior=behavior, max_stdout_bytes=1024, max_stderr_bytes=1024)

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "completed"
    expected_status = {
        "worker_unavailable": "failed",
        "acceptance_failed": "review",  # Contract: owner review, no retry.
        "internal": "queued",
    }
    assert outcome.status == expected_status[code]
    assert executor.run_calls == [("run", "fixture-model")]
    stored = _stored(db_session, task_id)
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == code
    assert stored["output"] == {"text": "", "files": []}
    assert db_session.get(Task, task_id).attempt == 1
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    details = list(db_session.scalars(select(Event.detail).where(Event.task_id == task_id)))
    safe_events = json.dumps(details)
    safe_log = (tmp_path / "runs" / str(task_id) / "1" / "ollama.log").read_text()
    assert "Synthetic failure output" not in safe_events + safe_log + json.dumps(stored)
    assert "x" * 64 not in safe_events + safe_log + json.dumps(stored)


def test_stdout_stays_in_result_without_leaking_into_logs_or_events(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(db_session)
    marker = "synthetic-output-marker"
    executor = RecordingExecutor(
        profile="simulation",
        simulation=OllamaSimulation(models=("fixture-model",), output_text=marker),
    )

    assert _runner(db_session, tmp_path, executor).run_once().status == "review"

    assert _stored(db_session, task_id)["output"]["text"] == marker
    details = list(db_session.scalars(select(Event.detail).where(Event.task_id == task_id)))
    assert marker not in json.dumps(details)
    assert marker not in (tmp_path / "runs" / str(task_id) / "1" / "ollama.log").read_text()


# Empty output fails local acceptance: contract sends it to review without retry.
@pytest.mark.parametrize(
    "behavior, attempts, final",
    [("empty", 1, TaskState.REVIEW), ("invalid_utf8", 2, TaskState.FAILED)],
)
def test_fake_cli_retries_are_separate_persisted_attempts_and_stop_at_policy_limit(
    db_session: Session, tmp_path: Path, behavior: str, attempts: int, final: TaskState
) -> None:
    task_id = _task(db_session)
    executor = _executor(behavior=behavior)
    runner = _runner(db_session, tmp_path, executor)
    for attempt in range(1, attempts + 1):
        outcome = runner.run_once()
        assert outcome.action == "completed"
        assert outcome.attempt == attempt
        assert outcome.status == (final.value if attempt == attempts else "queued")
        assert len(executor.run_calls) == attempt
        artifact = tmp_path / "runs" / str(task_id) / str(attempt) / "result.json"
        assert json.loads(artifact.read_text())["status"] == "failed"
    assert runner.run_once().action == "idle"
    assert len(executor.run_calls) == attempts
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == final
    assert (
        db_session.scalar(select(func.count()).select_from(Result).where(Result.task_id == task_id))
        == attempts
    )


def test_cli_timeout_ends_one_attempt_without_expiring_task_or_retrying(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(db_session)
    executor = RecordingExecutor(
        profile="simulation",
        simulation=OllamaSimulation(models=("fixture-model",), behavior="delay", delay_s=10),
        timeout_s=0.5,
    )
    started = time.monotonic()

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert time.monotonic() - started < 5
    assert outcome.action == "completed"
    assert outcome.status == "failed"
    assert executor.run_calls == [("run", "fixture-model")]
    assert _stored(db_session, task_id)["error"]["code"] == "timeout"
    assert db_session.get(Task, task_id).attempt == 1
    assert db_session.get(Task, task_id).deadline_at is None
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


@pytest.mark.parametrize("control", ["cancel", "deadline"])
def test_owner_control_stops_delayed_cli_without_persisting_late_output(
    db_session: Session, tmp_path: Path, control: str
) -> None:
    task_id = _task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    run_started = threading.Event()
    control_errors: list[Exception] = []

    class Observed(RecordingExecutor):
        def _call(self, arguments, **kwargs):
            if arguments[0] == "run":
                run_started.set()
            return super()._call(arguments, **kwargs)

    def issue_control():
        try:
            assert run_started.wait(timeout=5)
            time.sleep(0.1)
            with factory() as session:
                task = session.get(Task, task_id)
                if control == "cancel":
                    request_operator_cancel(session, task=task, reason="test")
                else:
                    task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()
        except Exception as error:
            control_errors.append(error)

    controller = threading.Thread(target=issue_control)
    executor = Observed(
        profile="simulation",
        simulation=OllamaSimulation(models=("fixture-model",), behavior="delay", delay_s=10),
    )
    runner = _runner(db_session, tmp_path, executor, factory=factory)
    controller.start()
    started = time.monotonic()
    try:
        outcome = runner.run_once()
    finally:
        controller.join(timeout=6)

    assert not controller.is_alive()
    assert control_errors == []
    assert time.monotonic() - started < 5
    assert outcome.action == ("cancel" if control == "cancel" else "expired")
    assert executor.run_calls == [("run", "fixture-model")]
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == (TaskState.CANCELLED if control == "cancel" else TaskState.EXPIRED)
    assert task.attempt == 1
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def test_failed_preflight_does_not_claim_or_expose_exception_contents(
    db_session: Session, tmp_path: Path
) -> None:
    task_id = _task(db_session)

    class Unavailable(RecordingExecutor):
        def availability(self, models):
            raise RuntimeError("synthetic-private-preflight-detail")

    executor = Unavailable(
        profile="simulation", simulation=OllamaSimulation(models=("fixture-model",))
    )
    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "error"
    assert outcome.error_code == "lane_preflight_failed"
    assert "synthetic-private-preflight-detail" not in repr(outcome)
    assert executor.calls == []
    _assert_unclaimed(db_session, task_id)


def test_two_independent_ollama_runners_complete_twenty_tasks_once(
    db_session: Session, tmp_path: Path
) -> None:
    update_settings(db_session, {"max_parallel": 2})
    db_session.commit()
    task_ids = {_task(db_session) for _ in range(20)}
    start_together = threading.Barrier(2)
    first_executions = threading.Barrier(2)
    state_lock = threading.Lock()
    completed: list[UUID] = []
    active_executions = 0
    max_active_executions = 0

    class ConcurrentExecutor(RecordingExecutor):
        first_execution = True

        def execute(self, context):
            nonlocal active_executions, max_active_executions
            with state_lock:
                active_executions += 1
                max_active_executions = max(max_active_executions, active_executions)
            try:
                if self.first_execution:
                    self.first_execution = False
                    first_executions.wait(timeout=10)
                return super().execute(context)
            finally:
                with state_lock:
                    active_executions -= 1

    executors = [
        ConcurrentExecutor(
            profile="simulation",
            simulation=OllamaSimulation(models=("fixture-model",), behavior="delay", delay_s=0.04),
        )
        for _ in range(2)
    ]
    runners = [_runner(db_session, tmp_path, executor) for executor in executors]

    def drain(runner: OllamaLaneRunner) -> list[UUID]:
        start_together.wait(timeout=10)
        own_tasks: list[UUID] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with state_lock:
                if len(completed) == len(task_ids):
                    return own_tasks
            outcome = runner.run_once()
            assert outcome.action in {"idle", "completed"}, outcome
            if outcome.action == "completed":
                assert outcome.status == "review"
                assert outcome.attempt == 1
                assert outcome.task_id in task_ids
                with state_lock:
                    completed.append(outcome.task_id)
                own_tasks.append(outcome.task_id)
            else:
                time.sleep(0.005)
        raise AssertionError("Concurrent simulated runners did not drain the task queue")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(drain, runner) for runner in runners]
        processed = [future.result(timeout=40) for future in futures]

    assert all(processed)
    assert len(completed) == 20
    assert set(completed) == task_ids
    assert max_active_executions == 2
    assert sum(len(executor.run_calls) for executor in executors) == 20
    db_session.expire_all()
    tasks = list(db_session.scalars(select(Task)))
    assert len(tasks) == 20
    assert all(task.status == TaskState.REVIEW and task.attempt == 1 for task in tasks)
    result_counts = dict(
        db_session.execute(select(Result.task_id, func.count()).group_by(Result.task_id)).all()
    )
    assert result_counts == dict.fromkeys(task_ids, 1)
    claim_counts = dict(
        db_session.execute(
            select(Event.task_id, func.count())
            .where(Event.event_type == "task.acquire")
            .group_by(Event.task_id)
        ).all()
    )
    assert claim_counts == dict.fromkeys(task_ids, 1)
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 1
    health = get_lane_status(db_session, lane=Worker.OLLAMA)
    assert health.status == "ok"
    assert health.reason == "simulation_ready"
    assert health.updated_at <= health.last_success_at
    assert health.probe_generation > 0


def test_simulation_after_live_failure_preserves_live_evidence(db_session, tmp_path, monkeypatch):
    from app.core.schemas import ErrorCode, ResultStatus
    from app.workers.lane_status import record_completed_attempt

    monkeypatch.setenv("DECK_LIVE_ENABLED", "true")
    record_completed_attempt(
        db_session,
        lane=Worker.OLLAMA,
        status=ResultStatus.FAILED,
        error_code=ErrorCode.TIMEOUT,
        execution_profile="live",
    )
    db_session.commit()
    task_id = _task(db_session)
    before = db_session.get(LaneStatus, "ollama")
    failed_at = before.last_error_at
    db_session.commit()
    assert _runner(db_session, tmp_path, _executor()).run_once().status == "review"
    db_session.expire_all()
    result = db_session.scalar(select(Result).where(Result.task_id == task_id))
    assert result.execution_profile == "simulation"
    assert result.payload["checks"][-1] == {
        "type": "execution_profile",
        "passed": False,
        "detail": "simulation",
    }
    row = db_session.get(LaneStatus, "ollama")
    assert row.completed_attempts == row.consecutive_failures == row.simulated_attempts == 1
    assert row.last_error_at == failed_at and row.last_attempt_success_at is None
    event = db_session.scalar(
        select(Event).where(Event.task_id == task_id, Event.event_type == "task.succeed")
    )
    assert event.detail["execution_profile"] == "simulation"
