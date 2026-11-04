"""Only finally accepted results contribute to local lane execution counters."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.schemas import Worker
from app.db.models import LaneStatus
from app.db.transitions import LeaseOwnershipError
from app.db.worker_ops import WorkerOperationError, submit_worker_result
from app.workers.lane_status import LaneHealthState, update_lane_status
from tests.integration.test_api import api_client as api_client
from tests.integration.test_api import owner_token as owner_token
from tests.integration.test_api import worker_token as worker_token
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_runner_api import _claim, _identity, _owner, _result, _worker


def running(client):
    payload = _minimal_task_payload(uuid4(), max_attempts=1)
    payload["data_class"] = "local_only"
    payload["fallback_chain"] = ["ollama"]
    payload["acceptance"] = [{"type": "must_contain", "value": "Completed"}]
    assert client.post("/api/v1/tasks", json=payload, headers=_owner()).status_code == 201
    assert (
        client.post(f"/api/v1/tasks/{payload['task_id']}/approve", headers=_owner()).status_code
        == 200
    )
    envelope = _claim(client)
    assert (
        client.post("/api/v1/worker/start", json=_identity(envelope), headers=_worker()).status_code
        == 200
    )
    return envelope


def submit(client, envelope, *, error=None, output="Completed"):
    result = _result(envelope, output=output)
    if error:
        result.update(status="failed", error={"code": error, "message": "Synthetic failure"})
    return client.post(
        "/api/v1/worker/result",
        json={"lease_id": envelope["lease"]["lease_id"], "result": result},
        headers=_worker(),
    )


def submit_live(session, envelope, *, error=None, output="Completed"):
    """Exercise trusted host ingestion; public HTTP workers cannot attest live."""
    result = _result(envelope, output=output)
    if error:
        result.update(status="failed", error={"code": error, "message": "Synthetic failure"})
    with Session(session.get_bind()) as writer:
        try:
            submit_worker_result(
                writer,
                lease_id=UUID(envelope["lease"]["lease_id"]),
                result_payload=result,
                execution_profile="live",
            )
            writer.commit()
        except (WorkerOperationError, LeaseOwnershipError):
            writer.rollback()
            return SimpleNamespace(status_code=409)
    return SimpleNamespace(status_code=200)


def attempt_evidence(session):
    row = session.get(LaneStatus, "ollama", populate_existing=True)
    assert row is not None
    return {
        name: getattr(row, name)
        for name in (
            "simulated_attempts",
            "completed_attempts",
            "consecutive_failures",
            "last_error_code",
            "last_error_at",
            "last_attempt_at",
            "last_attempt_success_at",
        )
    }


def test_single_accepted_success_counts_exactly_once(api_client, db_session):
    envelope = running(api_client)
    assert submit_live(db_session, envelope).status_code == 200
    evidence = attempt_evidence(db_session)
    assert evidence["completed_attempts"] == 1
    assert evidence["consecutive_failures"] == 0
    assert evidence["last_error_code"] is evidence["last_error_at"] is None
    assert evidence["last_attempt_at"] is not None
    assert evidence["last_attempt_at"] == evidence["last_attempt_success_at"]


def test_three_failures_then_success_count_only_completed_results(api_client, db_session):
    for count in range(1, 4):
        envelope = running(api_client)
        assert submit_live(db_session, envelope, error="timeout").status_code == 200
        row = db_session.get(LaneStatus, "ollama", populate_existing=True)
        assert row.completed_attempts == row.consecutive_failures == count
        assert row.last_error_code == "timeout"
        assert row.last_attempt_success_at is None
        db_session.commit()
    failed = attempt_evidence(db_session)
    assert failed["completed_attempts"] == failed["consecutive_failures"] == 3
    assert failed["last_error_at"] == failed["last_attempt_at"]
    envelope = running(api_client)
    assert submit_live(db_session, envelope).status_code == 200
    row = db_session.get(LaneStatus, "ollama", populate_existing=True)
    assert row.completed_attempts == 4 and row.consecutive_failures == 0
    assert row.last_attempt_success_at is not None
    assert row.last_success_at is None
    assert row.last_error_code == "timeout"
    assert row.last_error_at == failed["last_error_at"]
    assert row.last_attempt_at == row.last_attempt_success_at > row.last_error_at


@pytest.mark.parametrize("error", [None, "timeout"], ids=["success", "failure"])
def test_duplicate_and_preflight_leave_all_attempt_evidence_unchanged(
    api_client, db_session, error
):
    envelope = running(api_client)
    assert submit_live(db_session, envelope, error=error).status_code == 200
    before = attempt_evidence(db_session)
    assert before["completed_attempts"] == 1
    assert before["consecutive_failures"] == (1 if error else 0)
    assert submit_live(db_session, envelope, error=error).status_code != 200
    assert attempt_evidence(db_session) == before
    row = update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    assert attempt_evidence(db_session) == before
    assert row.last_success_at is not None
    assert row.last_attempt_at < row.last_success_at


@pytest.mark.parametrize("expired", ["lease", "deadline"])
def test_late_success_preserves_all_existing_attempt_evidence(api_client, db_session, expired):
    assert submit_live(db_session, running(api_client)).status_code == 200
    assert submit_live(db_session, running(api_client), error="timeout").status_code == 200
    before = attempt_evidence(db_session)
    assert before["completed_attempts"] == 2 and before["consecutive_failures"] == 1
    assert before["last_error_code"] == "timeout"
    assert all(before[name] is not None for name in before if name.endswith("_at"))
    envelope = running(api_client)
    if expired == "lease":
        db_session.execute(
            text("UPDATE leases SET expires_at=clock_timestamp()-interval '1s' WHERE task_id=:id"),
            {"id": envelope["task"]["id"]},
        )
    else:
        db_session.execute(
            text("UPDATE tasks SET deadline_at=clock_timestamp()-interval '1s' WHERE id=:id"),
            {"id": envelope["task"]["id"]},
        )
    db_session.commit()
    assert submit_live(db_session, envelope).status_code != 200
    assert attempt_evidence(db_session) == before


@pytest.mark.parametrize("boundary", ["lease", "cancel", "deadline", "fence"])
def test_rejected_result_does_not_change_counters(api_client, db_session, monkeypatch, boundary):
    envelope = running(api_client)
    task_id = envelope["task"]["id"]
    if boundary == "lease":
        db_session.execute(text("UPDATE leases SET expires_at=clock_timestamp()-interval '1s'"))
    elif boundary == "cancel":
        db_session.execute(
            text("UPDATE tasks SET cancel_requested=true WHERE id=:id"), {"id": task_id}
        )
    elif boundary == "deadline":
        db_session.execute(
            text("UPDATE tasks SET deadline_at=clock_timestamp()-interval '1s' WHERE id=:id"),
            {"id": task_id},
        )
    else:
        monkeypatch.setattr("app.db.worker_ops.fenced_transition_task", lambda *a, **k: None)
    db_session.commit()
    assert submit_live(db_session, envelope).status_code != 200
    assert db_session.scalar(select(LaneStatus)) is None


def test_acceptance_failure_is_counted_as_failure(api_client, db_session):
    envelope = running(api_client)
    assert submit_live(db_session, envelope, output="").status_code == 200
    row = db_session.get(LaneStatus, "ollama")
    assert row.completed_attempts == row.consecutive_failures == 1
    assert row.last_error_code == "acceptance_failed"
    assert row.last_attempt_success_at is None


def test_rollback_discards_counter_and_result(api_client, db_session):
    envelope = running(api_client)
    with Session(db_session.get_bind()) as writer:
        submit_worker_result(
            writer,
            lease_id=UUID(envelope["lease"]["lease_id"]),
            result_payload=_result(envelope),
            execution_profile="live",
        )
        assert writer.get(LaneStatus, "ollama").completed_attempts == 1
        writer.rollback()
    assert db_session.scalar(select(LaneStatus)) is None
    db_session.commit()
    assert submit_live(db_session, envelope).status_code == 200
    assert db_session.get(LaneStatus, "ollama").completed_attempts == 1


def test_concurrent_duplicate_acceptance_counts_exactly_once(api_client, db_session):
    envelope = running(api_client)
    engine = db_session.get_bind()

    def accept():
        with Session(engine) as writer:
            try:
                submit_worker_result(
                    writer,
                    lease_id=UUID(envelope["lease"]["lease_id"]),
                    result_payload=_result(envelope),
                    execution_profile="live",
                )
                writer.commit()
                return True
            except (WorkerOperationError, LeaseOwnershipError):
                writer.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: accept(), range(2))) == [False, True]
    assert db_session.get(LaneStatus, "ollama").completed_attempts == 1


def test_concurrent_distinct_results_on_same_lane_do_not_lose_counts(api_client, db_session):
    assert (
        api_client.patch(
            "/api/v1/control/settings", json={"changes": {"max_parallel": 2}}, headers=_owner()
        ).status_code
        == 200
    )
    envelopes = [running(api_client), running(api_client)]
    engine = db_session.get_bind()

    def accept(envelope):
        with Session(engine) as writer:
            submit_worker_result(
                writer,
                lease_id=UUID(envelope["lease"]["lease_id"]),
                result_payload=_result(envelope),
                execution_profile="live",
            )
            writer.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(accept, envelopes))
    row = db_session.get(LaneStatus, "ollama")
    assert row.completed_attempts == 2 and row.consecutive_failures == 0
    assert row.last_attempt_at == row.last_attempt_success_at


def test_counter_migration_preserves_observations_and_guards_evidence(db_session):
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    db_session.commit()
    command.downgrade(config, "20251103_0015")
    db_session.execute(
        text(
            "INSERT INTO lane_status (lane,status,reason,last_success_at) "
            "VALUES ('ollama','ok','simulation_ready',clock_timestamp())"
        )
    )
    db_session.commit()
    command.upgrade(config, "head")
    command.check(config)
    row = db_session.get(LaneStatus, "ollama")
    assert row.completed_attempts == row.consecutive_failures == 0
    assert row.last_success_at is not None and row.last_attempt_success_at is None
    row.completed_attempts = 1
    db_session.commit()
    with pytest.raises(RuntimeError, match="accepted attempt evidence"):
        command.downgrade(config, "20251103_0015")
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261104_0018"


def test_public_worker_cannot_promote_result_to_live(api_client, db_session):
    from app.db.models import Event, Result

    envelope = running(api_client)
    payload = _result(envelope)
    payload["checks"] = [{"type": "execution_profile", "passed": True, "detail": "live"}]
    response = api_client.post(
        "/api/v1/worker/result",
        json={"lease_id": envelope["lease"]["lease_id"], "result": payload},
        headers=_worker(),
    )
    assert response.status_code == 200
    row = db_session.scalar(select(Result))
    assert row.execution_profile == "remote"
    assert row.payload["checks"][-1] == {
        "type": "execution_profile",
        "passed": False,
        "detail": "remote",
    }
    event = db_session.scalar(select(Event).where(Event.event_type == "task.succeed"))
    assert event.detail["execution_profile"] == "remote"
    evidence = attempt_evidence(db_session)
    assert evidence["simulated_attempts"] == 1 and evidence["completed_attempts"] == 0
    assert evidence["last_attempt_success_at"] is None


def test_provenance_migration_classifies_old_results_and_removes_unattested_success(
    api_client, db_session
):
    import json

    from app.db.models import Result

    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    envelope = running(api_client)
    payload = _result(envelope)
    db_session.commit()
    command.downgrade(config, "20261104_0017")
    try:
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261104_0017"
        assert (
            db_session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND "
                    "((table_name='ui_sessions' AND column_name='client_hash') OR "
                    "(table_name='ui_login_clients' AND column_name='page_requests'))"
                )
            )
            == 2
        )
        db_session.execute(
            text(
                "INSERT INTO results (id, task_id, attempt, payload) "
                "VALUES (:id, :task_id, 1, CAST(:payload AS jsonb))"
            ),
            {"id": uuid4(), "task_id": payload["task_id"], "payload": json.dumps(payload)},
        )
        db_session.execute(
            text(
                "INSERT INTO lane_status (lane,status,reason,completed_attempts,"
                "consecutive_failures,"
                "last_attempt_at,last_attempt_success_at,last_success_at) VALUES "
                "('ollama','ok','simulation_ready',7,0,clock_timestamp(),clock_timestamp(),clock_timestamp())"
            )
        )
        db_session.commit()
        command.upgrade(config, "head")
        command.check(config)
        result = db_session.scalar(select(Result))
        assert result.execution_profile == "unknown"
        row = db_session.get(LaneStatus, "ollama")
        assert row.simulated_attempts == 7 and row.completed_attempts == 0
        assert row.last_attempt_success_at is row.last_attempt_at is None
        assert row.last_success_at is not None
        db_session.commit()
        with pytest.raises(RuntimeError, match="execution provenance"):
            command.downgrade(config, "20261104_0017")
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261104_0018"
    finally:
        db_session.rollback()
        command.upgrade(config, "head")
