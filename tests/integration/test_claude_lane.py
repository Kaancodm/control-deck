"""Offline Claude admission preserves approval, data boundaries and host control."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.schemas import TaskV1, Worker
from app.db.models import Event, Lease, RedactionMap, Result, Task
from app.db.repository import approve_task, create_task_from_v1
from app.db.settings import read_settings, update_settings
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel
from app.workers import sandbox
from app.workers.claude_lane import ClaudeLaneRunner
from app.workers.lane_status import get_lane_status
from app.workers.lanes.claude import ClaudeExecutor, ClaudeSimulation
from app.workers.lanes.ollama import OllamaExecutor, OllamaSimulation
from app.workers.ollama_lane import OllamaLaneRunner
from app.workers.sandbox import CleanroomSimulation
from tests.integration.test_database import _minimal_task_payload


def _write_config(path: Path, *, models=None, enabled: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "lanes": {
                    "claude": {
                        "enabled": enabled,
                        "models": {"default": "fixture-model"} if models is None else models,
                    }
                },
            }
        )
    )


@pytest.fixture(autouse=True)
def router_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "router.yaml"
    _write_config(path)
    monkeypatch.setenv("DECK_ROUTER_FILE", str(path))
    literals = tmp_path / "terms.txt"
    literals.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(literals))
    return path


@pytest.fixture
def process_calls(monkeypatch):
    calls = []
    original = sandbox.run_process

    def observe(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(sandbox, "run_process", observe)
    return calls


def _runs(calls):
    return [(command, arguments) for command, arguments in calls if command[2] == "run"]


def _task(
    session: Session,
    *,
    approved: bool = True,
    data_class: str = "cloud_ok",
    text: str = "ordinary synthetic text",
    acceptance=None,
    max_attempts: int = 3,
) -> UUID:
    payload = _minimal_task_payload(uuid4(), max_attempts=max_attempts)
    payload["fallback_chain"] = ["claude", "ollama"]
    payload["data_class"] = data_class
    payload["instructions"] = text
    payload["acceptance"] = acceptance or []
    task = create_task_from_v1(session, TaskV1.model_validate(payload))
    if approved:
        approve_task(session, task)
    task_id = task.id
    session.commit()
    return task_id


def _executor(*, models=("fixture-model",), **cleanroom):
    return ClaudeExecutor(
        profile="simulation",
        simulation=ClaudeSimulation(models=models, cleanroom=CleanroomSimulation(**cleanroom)),
    )


def _runner(session, tmp_path, executor, *, factory=None):
    return ClaudeLaneRunner(
        factory or sessionmaker(bind=session.get_bind(), autoflush=False),
        runs_root=tmp_path / "runs",
        executor=executor,
        lease_ttl=timedelta(seconds=10),
        heartbeat_interval_s=0.03,
    )


def _stored(session: Session, task_id: UUID):
    session.expire_all()
    return session.scalar(select(Result.payload).where(Result.task_id == task_id))


def _assert_unclaimed(
    session: Session, task_id: UUID, *, status: TaskState = TaskState.QUEUED
) -> None:
    session.expire_all()
    task = session.get(Task, task_id)
    assert task.status == status
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


def test_owner_approval_precedes_one_claude_attempt_and_acceptance_review(
    db_session, tmp_path, process_calls
):
    task_id = _task(
        db_session,
        approved=False,
        acceptance=[{"type": "must_contain", "value": "simulation-ok"}],
    )
    runner = _runner(db_session, tmp_path, _executor())
    assert runner.run_once().action == "idle"
    assert process_calls == []
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == TaskState.AWAITING_APPROVAL
    approve_task(db_session, task)
    db_session.commit()

    outcome = runner.run_once()

    assert outcome.action == "completed"
    assert outcome.status == "review"
    assert outcome.task_id == task_id
    assert outcome.attempt == 1
    assert len(_runs(process_calls)) == 1
    assert [command[2] for command, _ in process_calls] == ["info", "run", "rm"]
    result = _stored(db_session, task_id)
    assert result["worker"] == "claude"
    assert result["status"] == "succeeded"
    assert result["output"]["files"] == []
    assert result["checks"][0]["passed"] is True
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    health = get_lane_status(db_session, lane=Worker.CLAUDE)
    assert health.status == "ok"
    assert health.reason == "simulation_ready"
    assert health.last_success_at is not None


def test_only_redacted_archived_bytes_cross_the_claude_process_boundary(
    db_session, tmp_path, process_calls
):
    original = "synthetic" + "@" + "example.invalid"
    task_id = _task(db_session, data_class="cloud_redacted", text="Reply to " + original)

    assert _runner(db_session, tmp_path, _executor(behavior="echo")).run_once().status == "review"

    directory = tmp_path / "runs" / str(task_id) / "1"
    archived = (directory / "payload.sent.md").read_bytes()
    command, arguments = _runs(process_calls)[0]
    assert arguments["input_bytes"] == archived
    assert original.encode() not in archived
    assert b"[[CD_" in archived
    assert original not in json.dumps(command)
    assert original not in json.dumps(arguments["env"])
    result = _stored(db_session, task_id)
    assert original in result["output"]["text"]
    assert "[[CD_" not in result["output"]["text"]
    assert result["payload_hash"] == hashlib.sha256(archived).hexdigest()
    assert result["redaction"] == {"applied": True, "placeholders": 1}
    assert json.loads((directory / "result.json").read_bytes()) == result
    assert (
        db_session.scalar(
            select(func.count()).select_from(RedactionMap).where(RedactionMap.task_id == task_id)
        )
        == 1
    )
    events = list(db_session.scalars(select(Event.detail).where(Event.task_id == task_id)))
    assert original not in json.dumps(events)
    assert original not in (directory / "claude.log").read_text()


def test_local_only_is_skipped_while_claude_executes_the_next_cloud_task(
    db_session, tmp_path, process_calls
):
    local_id = _task(db_session, data_class="local_only", text="synthetic-local-only-marker")
    cloud_id = _task(db_session)

    outcome = _runner(db_session, tmp_path, _executor()).run_once()

    assert outcome.task_id == cloud_id
    assert outcome.status == "review"
    assert len(_runs(process_calls)) == 1
    assert b"synthetic-local-only-marker" not in _runs(process_calls)[0][1]["input_bytes"]
    db_session.expire_all()
    local = db_session.get(Task, local_id)
    assert local.status == TaskState.QUEUED
    assert local.attempt == 0
    assert local.lane == "ollama"
    assert not (tmp_path / "runs" / str(local_id)).exists()


@pytest.mark.parametrize(
    ("executor", "reason"),
    [
        (ClaudeExecutor(), "live_profile_unavailable"),
        (_executor(rootless=False), "rootless_required"),
        (_executor(cli_version="0.0.0"), "cli_version_unsupported"),
        (_executor(models=()), "model_not_available"),
    ],
)
def test_unavailable_claude_profile_does_not_consume_attempts(
    db_session, tmp_path, process_calls, executor, reason
):
    task_id = _task(db_session)

    outcome = _runner(db_session, tmp_path, executor).run_once()

    assert outcome.action == "unavailable"
    assert outcome.error_code == reason
    assert process_calls == []
    _assert_unclaimed(db_session, task_id)
    health = get_lane_status(db_session, lane=Worker.CLAUDE)
    assert health.status == "unavailable"
    assert health.reason == reason
    assert health.last_success_at is None


@pytest.mark.parametrize("models", [{}, {"summarize": "fixture-model"}])
def test_missing_task_model_never_reaches_claude(
    db_session, tmp_path, router_file, process_calls, models
):
    task_id = _task(db_session)
    _write_config(router_file, models=models)

    outcome = _runner(db_session, tmp_path, _executor()).run_once()

    assert outcome.action == ("unavailable" if not models else "idle")
    assert process_calls == []
    _assert_unclaimed(db_session, task_id, status=TaskState.BLOCKED if models else TaskState.QUEUED)
    if models:
        detail = db_session.scalar(
            select(Event.detail).where(Event.task_id == task_id, Event.event_type == "task.block")
        )
        assert detail == {
            "reason": "routing",
            "code": "model_not_configured",
            "lane": "claude",
            "kind": "custom",
            "actor": "system",
            "attempt": 0,
        }
        health = get_lane_status(db_session, lane=Worker.CLAUDE)
        assert health.status == "unavailable" and health.reason == "model_not_configured"


@pytest.mark.parametrize("setting", ["paused", "stop_requested", "lanes.claude.paused"])
def test_paused_or_stopped_lane_never_starts_a_process(
    db_session, tmp_path, process_calls, setting
):
    task_id = _task(db_session)
    update_settings(db_session, {setting: True})
    db_session.commit()

    outcome = _runner(db_session, tmp_path, _executor()).run_once()

    assert outcome.action == "idle"
    assert outcome.error_code == "lane_paused"
    assert process_calls == []
    _assert_unclaimed(db_session, task_id)


def test_disabled_claude_lane_defers_the_waiting_task(
    db_session, tmp_path, router_file, process_calls
):
    task_id = _task(db_session)
    _write_config(router_file, enabled=False)

    outcome = _runner(db_session, tmp_path, _executor()).run_once()

    assert outcome.action == "idle"
    assert outcome.error_code == "lane_disabled"
    assert process_calls == []
    _assert_unclaimed(db_session, task_id)


def test_processes_execute_without_an_open_main_thread_transaction(
    db_session, tmp_path, monkeypatch
):
    task_id = _task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    sessions = []
    observed = []
    original = sandbox.run_process

    def tracked_factory():
        session = factory()
        sessions.append((threading.get_ident(), session))
        return session

    def inspect(command, **kwargs):
        assert all(
            not session.in_transaction()
            for thread_id, session in sessions
            if thread_id == threading.get_ident()
        )
        observed.append(command[2])
        if command[2] == "run":
            with factory() as session:
                task = session.get(Task, task_id)
                assert task.status == TaskState.RUNNING
                assert task.approved_at is not None
        return original(command, **kwargs)

    monkeypatch.setattr(sandbox, "run_process", inspect)

    assert (
        _runner(db_session, tmp_path, _executor(), factory=tracked_factory).run_once().status
        == "review"
    )
    assert observed == ["info", "run", "rm"]


def test_acceptance_failure_returns_to_the_core_without_a_hidden_claude_retry(
    db_session, tmp_path, process_calls
):
    task_id = _task(
        db_session, acceptance=[{"type": "must_contain", "value": "missing expected phrase"}]
    )

    outcome = _runner(db_session, tmp_path, _executor()).run_once()

    assert outcome.action == "completed"
    # Contract (Etappe 10): acceptance_failed -> review, no retry.
    assert outcome.status == "review"
    assert len(_runs(process_calls)) == 1
    result = _stored(db_session, task_id)
    assert result["error"]["code"] == "acceptance_failed"
    assert result["checks"][0]["passed"] is False
    assert db_session.get(Task, task_id).attempt == 1
    assert (
        db_session.scalar(
            select(Event.detail).where(Event.task_id == task_id, Event.event_type == "task.retry")
        )
        is None
    )
    review = db_session.scalar(
        select(Event.detail).where(Event.task_id == task_id, Event.event_type == "task.review")
    )
    assert review["reason"] == "acceptance_failed"


@pytest.mark.parametrize(
    ("behavior", "code", "health"),
    [("auth", "auth_required", "auth_required"), ("policy", "policy_blocked", "paused")],
)
def test_provider_errors_are_safe_results_and_health_observations(
    db_session, tmp_path, process_calls, behavior, code, health
):
    task_id = _task(db_session)

    outcome = _runner(db_session, tmp_path, _executor(behavior=behavior)).run_once()

    assert outcome.action == "completed"
    assert len(_runs(process_calls)) == 1
    result = _stored(db_session, task_id)
    assert result["error"]["code"] == code
    assert result["output"] == {"text": "", "files": []}
    assert get_lane_status(db_session, lane=Worker.CLAUDE).status == health
    events = list(db_session.scalars(select(Event.detail).where(Event.task_id == task_id)))
    audit = json.dumps(events) + (tmp_path / "runs" / str(task_id) / "1" / "claude.log").read_text()
    assert "Synthetic provider failure" not in audit + json.dumps(result)


def test_http_400_refusal_pauses_the_claude_lane_and_sets_the_lane_flag(
    db_session, tmp_path, process_calls
):
    task_id = _task(db_session)
    runner = _runner(db_session, tmp_path, _executor(behavior="policy_400"))

    outcome = runner.run_once()

    assert outcome.action == "completed"
    assert len(_runs(process_calls)) == 1
    result = _stored(db_session, task_id)
    assert result["error"]["code"] == "policy_blocked"
    assert result["output"] == {"text": "", "files": []}
    assert read_settings(db_session).lane_paused[Worker.CLAUDE]
    health = get_lane_status(db_session, lane=Worker.CLAUDE)
    assert health.status == "paused"
    assert health.reason == "simulation_policy_blocked"
    db_session.commit()
    calls_before = len(process_calls)

    _task(db_session)
    assert runner.run_once().error_code == "lane_paused"
    assert len(process_calls) == calls_before


def test_paused_poll_preserves_authentication_failure_until_owner_resumes(
    db_session, tmp_path, process_calls
):
    _task(db_session)
    runner = _runner(db_session, tmp_path, _executor(behavior="auth"))
    assert runner.run_once().action == "completed"
    update_settings(db_session, {"lanes.claude.paused": True})
    db_session.commit()
    calls_before = len(process_calls)

    assert runner.run_once().error_code == "lane_paused"
    db_session.expire_all()
    health = get_lane_status(db_session, lane=Worker.CLAUDE)
    assert health.status == "auth_required"
    assert health.reason == "simulation_auth_required"
    assert len(process_calls) == calls_before


@pytest.mark.parametrize("control", ["cancel", "deadline", "lease_loss"])
def test_active_claude_stops_on_owner_control_or_lease_loss_without_late_result(
    db_session, tmp_path, monkeypatch, control
):
    task_id = _task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    run_started = threading.Event()
    controls_failed = []
    calls = []
    original = sandbox.run_process

    def observe(command, **kwargs):
        calls.append(command[2])
        if command[2] == "run":
            run_started.set()
        return original(command, **kwargs)

    monkeypatch.setattr(sandbox, "run_process", observe)

    def issue_control():
        try:
            assert run_started.wait(timeout=5)
            time.sleep(0.1)
            with factory() as session:
                task = session.get(Task, task_id)
                if control == "cancel":
                    request_operator_cancel(session, task=task, reason="test")
                elif control == "deadline":
                    task.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    session.delete(task.lease)
                session.commit()
        except Exception as error:
            controls_failed.append(error)

    controller = threading.Thread(target=issue_control)
    controller.start()
    started = time.monotonic()
    try:
        outcome = _runner(
            db_session, tmp_path, _executor(behavior="delay", delay_s=10), factory=factory
        ).run_once()
    finally:
        controller.join(timeout=6)

    assert not controller.is_alive()
    assert controls_failed == []
    assert time.monotonic() - started < 5
    assert (
        outcome.action
        == {"cancel": "cancel", "deadline": "expired", "lease_loss": "lost_lease"}[control]
    )
    assert calls == ["info", "run", "rm"]
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    if control == "cancel":
        assert task.status == TaskState.CANCELLED
    elif control == "deadline":
        assert task.status == TaskState.EXPIRED
    else:
        assert task.status == TaskState.RUNNING
        assert task.deadline_at is None
    assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def _enable_fallback(path: Path) -> None:
    configuration = json.loads(path.read_text())
    configuration["lanes"]["ollama"] = {"models": {"default": "fixture-model"}}
    path.write_text(json.dumps(configuration))


def _ollama_runner(session: Session, tmp_path: Path) -> OllamaLaneRunner:
    return OllamaLaneRunner(
        sessionmaker(bind=session.get_bind(), autoflush=False),
        runs_root=tmp_path / "runs",
        executor=OllamaExecutor(
            profile="simulation", simulation=OllamaSimulation(models=("fixture-model",))
        ),
        lease_ttl=timedelta(seconds=10),
        heartbeat_interval_s=0.03,
    )


@pytest.mark.parametrize("behavior", ["auth", "policy"])
def test_provider_refusal_pauses_claude_then_falls_back_through_the_core(
    db_session, tmp_path, router_file, process_calls, behavior
):
    _enable_fallback(router_file)
    task_id = _task(db_session)

    assert _runner(db_session, tmp_path, _executor(behavior=behavior)).run_once().status == "queued"

    db_session.expire_all()
    assert db_session.get(Task, task_id).retry_state["cursor"] == 1
    assert read_settings(db_session).lane_paused[Worker.CLAUDE]
    db_session.commit()
    outcome = _ollama_runner(db_session, tmp_path).run_once()
    assert outcome.task_id == task_id and outcome.attempt == 2 and outcome.status == "review"
    assert len(_runs(process_calls)) == 1
    db_session.expire_all()
    stored = list(
        db_session.scalars(
            select(Result.payload).where(Result.task_id == task_id).order_by(Result.attempt)
        )
    )
    assert [result["worker"] for result in stored] == ["claude", "ollama"]
    assert read_settings(db_session).lane_paused[Worker.CLAUDE]


def test_claude_rate_limit_backoff_survives_new_runners_then_advances(
    db_session, tmp_path, router_file, process_calls
):
    _enable_fallback(router_file)
    task_id = _task(db_session, max_attempts=5)
    for attempt, delay in ((1, 60), (2, 240)):
        before = db_session.scalar(select(func.clock_timestamp()))
        db_session.commit()
        outcome = _runner(db_session, tmp_path, _executor(behavior="rate_limit")).run_once()
        assert outcome.status == "queued" and outcome.attempt == attempt
        db_session.expire_all()
        task = db_session.get(Task, task_id)
        assert task.retry_state["pinned_worker"] == "claude"
        assert task.retry_state["rate_limits"] == {"claude": attempt}
        assert task.next_attempt_at >= before + timedelta(seconds=delay)
        assert _runner(db_session, tmp_path, _executor()).run_once().action == "idle"
        assert _ollama_runner(db_session, tmp_path).run_once().action == "idle"
        assert len(_runs(process_calls)) == attempt
        task.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
    outcome = _runner(db_session, tmp_path, _executor(behavior="rate_limit")).run_once()
    assert outcome.status == "queued" and outcome.attempt == 3
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.retry_state["cursor"] == 1
    assert task.next_attempt_at <= db_session.scalar(select(func.clock_timestamp()))
    db_session.commit()
    outcome = _ollama_runner(db_session, tmp_path).run_once()
    assert outcome.status == "review" and outcome.attempt == 4
    db_session.expire_all()
    assert db_session.get(Task, task_id).next_attempt_at is None
    assert len(_runs(process_calls)) == 3


def test_empty_claude_completion_goes_to_review_without_fallback(
    db_session, tmp_path, router_file, process_calls
):
    _enable_fallback(router_file)
    task_id = _task(db_session, max_attempts=2)
    assert _runner(db_session, tmp_path, _executor(behavior="empty")).run_once().status == "review"
    assert _ollama_runner(db_session, tmp_path).run_once().action == "idle"
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.attempt == 1 and task.retry_state["pinned_worker"] == "claude"
    assert task.deadline_at is None
    assert len(_runs(process_calls)) == 1
    results = list(db_session.scalars(select(Result.payload).where(Result.task_id == task_id)))
    assert all(result["error"]["code"] == "acceptance_failed" for result in results)


def test_claude_transport_timeout_falls_back_without_creating_a_deadline(
    db_session, tmp_path, router_file, process_calls
):
    _enable_fallback(router_file)
    task_id = _task(db_session)
    adapter = ClaudeExecutor(
        profile="simulation",
        simulation=ClaudeSimulation(
            ("fixture-model",), CleanroomSimulation(behavior="delay", delay_s=5)
        ),
        timeout_s=1,
    )
    assert _runner(db_session, tmp_path, adapter).run_once().status == "queued"
    assert _ollama_runner(db_session, tmp_path).run_once().status == "review"
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.attempt == 2 and task.deadline_at is None
    assert len(_runs(process_calls)) == 1
    result = db_session.scalar(
        select(Result.payload).where(Result.task_id == task_id, Result.attempt == 1)
    )
    assert result["error"]["code"] == "timeout"
