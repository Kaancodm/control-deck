"""Live HTTP orchestration against a local stub and disposable test database."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.schemas import Worker
from app.db.models import Event, LaneStatus, Lease, Result, Task
from app.db.settings import update_settings
from app.db.state_machine import TaskState
from app.db.transitions import request_operator_cancel
from app.workers.lanes.ollama import OllamaExecutor
from app.workers.ollama_lane import OllamaLaneRunner
from tests.integration.test_ollama_lane import _runner, _stored, _task, _write_config
from tests.support.ollama_stub import OllamaStub

MODEL = "fixture:configured"


@pytest.fixture(autouse=True)
def live_router(tmp_path, monkeypatch):
    path = tmp_path / "router.yaml"
    _write_config(path, models={"default": MODEL})
    monkeypatch.setenv("DECK_ROUTER_FILE", str(path))
    blocklist = tmp_path / "terms.txt"
    blocklist.write_text("")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(blocklist))


def _executor(stub, **kwargs):
    return OllamaExecutor(profile="live", url=stub.url, **kwargs)


def test_live_attempt_reaches_review_with_provenance_and_no_log_text(db_session, tmp_path):
    prompt_marker, output_marker = "private-prompt-marker", "private-output-marker"
    task_id = _task(
        db_session,
        text=prompt_marker,
        acceptance=[{"type": "must_contain", "value": output_marker}],
    )
    with OllamaStub(models=(MODEL,), output=output_marker) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.status == "review"
    db_session.expire_all()
    stored = db_session.scalar(select(Result).where(Result.task_id == task_id))
    assert stored.execution_profile == "live"
    assert stored.payload["checks"][-1] == {
        "type": "execution_profile",
        "passed": True,
        "detail": "live",
    }
    assert stored.payload["checks"][0]["passed"] is True
    health = db_session.get(LaneStatus, Worker.OLLAMA)
    assert health.reason == "live_ready"
    assert health.completed_attempts == 1 and health.last_attempt_success_at is not None
    event = db_session.scalar(
        select(Event).where(Event.task_id == task_id, Event.event_type == "task.succeed")
    )
    assert event.detail["execution_profile"] == "live"
    assert event.detail["transport"] == "ollama_http"
    safe = (tmp_path / "runs" / str(task_id) / "1" / "ollama.log").read_text()
    safe += json.dumps(
        [e.detail for e in db_session.scalars(select(Event).where(Event.task_id == task_id))],
        default=str,
    )
    assert output_marker not in safe and prompt_marker not in safe
    assert not any(request["path"] == "/api/pull" for request in stub.requests)


def test_live_echo_rehydrates_locally_and_preserves_archived_payload(db_session, tmp_path):
    original = "synthetic" + "@" + "example.invalid"
    task_id = _task(db_session, data_class="cloud_redacted", text="Reply to " + original)
    with OllamaStub(models=(MODEL,), output="__echo__") as stub:
        assert _runner(db_session, tmp_path, _executor(stub)).run_once().status == "review"
    sent = next(
        request["body"]["messages"][0]["content"]
        for request in stub.requests
        if request["path"] == "/api/chat"
    )
    assert original not in sent and "[[CD_" in sent
    assert (
        sent.encode() == (tmp_path / "runs" / str(task_id) / "1" / "payload.sent.md").read_bytes()
    )
    assert original in _stored(db_session, task_id)["output"]["text"]


def test_missing_model_prevents_claim(db_session, tmp_path):
    task_id = _task(db_session)
    with OllamaStub(models=()) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.action == "unavailable" and outcome.error_code == "model_not_available"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.QUEUED
    assert db_session.get(Task, task_id).attempt == 0
    assert not any(request["path"] in {"/api/show", "/api/chat"} for request in stub.requests)


def test_model_disappearing_at_chat_is_failed_without_adapter_retry(db_session, tmp_path):
    task_id = _task(db_session)
    with OllamaStub(models=(MODEL,), status_by_path={"/api/chat": 404}) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.action == "completed"
    payload = _stored(db_session, task_id)
    assert payload["status"] == "failed" and payload["error"]["code"] == "worker_unavailable"
    assert payload["output"]["text"] == ""
    assert sum(request["path"] == "/api/chat" for request in stub.requests) == 1
    assert not any(request["path"] == "/api/pull" for request in stub.requests)


def test_length_routes_to_review_with_failed_completion_check(db_session, tmp_path):
    task_id = _task(db_session)
    chunks = [
        {
            "model": MODEL,
            "message": {"role": "assistant", "content": "partial"},
            "done": True,
            "done_reason": "length",
        }
    ]
    with OllamaStub(models=(MODEL,), chat_chunks=chunks) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.status == "review"
    payload = _stored(db_session, task_id)
    assert payload["error"]["code"] == "acceptance_failed"
    assert payload["output"]["text"] == ""
    assert any(
        check["type"] == "completion" and check["passed"] is False for check in payload["checks"]
    )
    assert db_session.get(LaneStatus, Worker.OLLAMA).last_attempt_success_at is None


def test_live_timeout_stores_no_partial_output_and_does_not_retry_adapter(db_session, tmp_path):
    task_id = _task(db_session)
    with OllamaStub(models=(MODEL,), initial_delay_s=2) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub, timeout_s=0.2)).run_once()
    assert outcome.action == "completed"
    payload = _stored(db_session, task_id)
    assert payload["error"]["code"] == "timeout" and payload["output"]["text"] == ""
    assert sum(request["path"] == "/api/chat" for request in stub.requests) == 1


def test_local_home_path_stays_plain_text(db_session, tmp_path):
    task_id = _task(db_session, text="Read /home/synthetic/private.txt")
    with OllamaStub(models=(MODEL,)) as stub:
        _runner(db_session, tmp_path, _executor(stub)).run_once()
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.REVIEW
    chat = next(request for request in stub.requests if request["path"] == "/api/chat")
    assert "/home/synthetic/private.txt" in chat["body"]["messages"][0]["content"]
    assert "images" not in chat["body"]["messages"][0]


def test_live_pause_stops_preflight_and_claim(db_session, tmp_path):
    task_id = _task(db_session)
    update_settings(db_session, {"paused": True})
    db_session.commit()
    with OllamaStub(models=(MODEL,)) as stub:
        assert _runner(db_session, tmp_path, _executor(stub)).run_once().action == "idle"
    assert stub.requests == []
    db_session.expire_all()
    assert db_session.get(Task, task_id).attempt == 0


def test_cancel_during_live_stream_discards_output(db_session, tmp_path):
    task_id = _task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    with OllamaStub(models=(MODEL,), chunk_delay_s=3, output="partial-marker") as stub:
        runner = OllamaLaneRunner(factory, runs_root=tmp_path / "runs", executor=_executor(stub))
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(runner.run_once()))
        thread.start()
        assert stub.chat_started.wait(5)
        with factory() as session:
            task = session.get(Task, task_id)
            request_operator_cancel(session, task=task)
            session.commit()
        thread.join(timeout=2)
        assert not thread.is_alive()
    db_session.expire_all()
    assert db_session.get(Task, task_id).status == TaskState.CANCELLED
    stored = _stored(db_session, task_id)
    if stored is not None:
        assert stored["output"]["text"] == ""
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0


def test_two_live_runners_complete_twenty_tasks_once(db_session, tmp_path):
    task_ids = {_task(db_session) for _ in range(20)}
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    with OllamaStub(models=(MODEL,)) as stub:

        def drain():
            runner = _runner(db_session, tmp_path, _executor(stub), factory=factory)
            outcomes = []
            for _ in range(21):
                outcome = runner.run_once()
                if outcome.action == "idle":
                    break
                assert outcome.action == "completed", outcome
                outcomes.append(outcome.task_id)
            return outcomes

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(drain) for _ in range(2)]
            completed = [task_id for future in futures for task_id in future.result()]
    assert len(completed) == 20 and set(completed) == task_ids
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Result)) == 20
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0
    assert all(task.status == TaskState.REVIEW for task in db_session.scalars(select(Task)))


def test_missing_live_model_does_not_block_available_task_and_context_is_configured(
    db_session, tmp_path
):
    (tmp_path / "router.yaml").write_text(
        json.dumps(
            {
                "lanes": {
                    "ollama": {
                        "num_ctx": 2048,
                        "models": {"default": MODEL, "summarize": "missing:configured"},
                    }
                }
            }
        )
    )
    missing_id = _task(db_session, kind="summarize")
    available_id = _task(db_session)
    with OllamaStub(
        models=(MODEL,), show_extra={"model_info": {"fixture.context_length": 8192}}
    ) as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.task_id == available_id and outcome.status == "review"
    db_session.expire_all()
    missing = db_session.get(Task, missing_id)
    assert missing.status == TaskState.QUEUED and missing.attempt == 0
    chats = [r["body"] for r in stub.requests if r["path"] == "/api/chat"]
    assert len(chats) == 1
    assert chats[0]["options"]["num_ctx"] == 2048


def test_nul_output_fails_the_attempt_instead_of_stranding_the_lease(db_session, tmp_path):
    # PostgreSQL JSONB cannot store U+0000, so this output must not reach persistence.
    task_id = _task(db_session)
    with OllamaStub(models=(MODEL,), output="before\u0000after") as stub:
        outcome = _runner(db_session, tmp_path, _executor(stub)).run_once()
    assert outcome.action == "completed"
    db_session.expire_all()
    assert db_session.get(Task, task_id).status != TaskState.RUNNING
    stored = _stored(db_session, task_id)
    assert stored["status"] == "failed"
    assert stored["output"]["text"] == ""
    assert stored["error"]["code"] == "internal"
