"""Only finally accepted results contribute to local lane execution counters."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.schemas import Worker
from app.db.models import Event, LaneStatus, Result, Task
from app.db.queue import reap_expired_leases
from app.db.state_machine import TaskState
from app.db.transitions import LeaseOwnershipError
from app.db.worker_ops import WorkerOperationError, submit_worker_result
from app.workers.lane_status import LaneHealthState, update_lane_status
from tests.integration.test_api import api_client as api_client
from tests.integration.test_api import owner_token as owner_token
from tests.integration.test_api import worker_token as worker_token
from tests.integration.test_database import _minimal_task_payload
from tests.integration.test_runner_api import _claim, _identity, _owner, _result, _worker


def running(client, *, max_attempts=1):
    payload = _minimal_task_payload(uuid4(), max_attempts=max_attempts)
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
            "remote_attempts",
            "unknown_attempts",
            "completed_attempts",
            "consecutive_failures",
            "last_error_code",
            "last_error_at",
            "last_attempt_at",
            "last_attempt_success_at",
        )
    }


def result_event_evidence(session, task_id):
    """Snapshot persisted evidence without relying on cached ORM identities."""
    return [
        [
            copy.deepcopy(dict(row))
            for row in session.execute(
                select(model.__table__).where(model.task_id == task_id).order_by(model.id)
            ).mappings()
        ]
        for model in (Result, Event)
    ]


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


def test_old_attempt_result_after_reclaim_is_fenced(api_client, db_session):
    old = running(api_client, max_attempts=2)
    task_id = UUID(old["task"]["id"])
    db_session.execute(
        text("UPDATE leases SET expires_at=clock_timestamp()-interval '1s' WHERE task_id=:id"),
        {"id": task_id},
    )
    db_session.commit()
    assert reap_expired_leases(db_session) == 1
    db_session.commit()

    current = _claim(api_client)
    assert current["task"]["id"] == old["task"]["id"]
    assert current["task"]["attempt"] == old["task"]["attempt"] + 1 == 2
    assert current["lease"]["lease_id"] != old["lease"]["lease_id"]
    assert (
        api_client.post(
            "/api/v1/worker/start", json=_identity(current), headers=_worker()
        ).status_code
        == 200
    )
    before = result_event_evidence(db_session, task_id)
    assert submit_live(db_session, old).status_code == 409
    stale_lease = copy.deepcopy(current)
    stale_lease["lease"]["lease_id"] = old["lease"]["lease_id"]
    assert submit_live(db_session, stale_lease).status_code == 409
    assert result_event_evidence(db_session, task_id) == before
    lost = db_session.scalars(select(Result).order_by(Result.attempt)).all()
    assert len(lost) == 1 and lost[0].attempt == 1
    assert lost[0].execution_profile == "unknown"
    assert lost[0].payload["error"]["code"] == "worker_unavailable"
    lost_health = db_session.get(LaneStatus, "ollama", populate_existing=True)
    assert lost_health is not None
    assert lost_health.unknown_attempts == 1
    assert lost_health.consecutive_failures == 1
    assert lost_health.completed_attempts == 0
    assert lost_health.last_error_code == "worker_unavailable"

    assert submit_live(db_session, current).status_code == 200
    accepted = attempt_evidence(db_session)
    assert accepted["completed_attempts"] == 1
    assert accepted["simulated_attempts"] == accepted["remote_attempts"] == 0
    assert accepted["unknown_attempts"] == 1
    assert accepted["consecutive_failures"] == 0
    stored = db_session.scalar(select(Result).where(Result.task_id == task_id, Result.attempt == 2))
    assert stored is not None and stored.execution_profile == "live"
    task = db_session.get(Task, task_id, populate_existing=True)
    assert task.status == TaskState.REVIEW and task.attempt == 2
    before = result_event_evidence(db_session, task_id)
    assert submit_live(db_session, old).status_code == 409
    assert submit_live(db_session, current).status_code == 409
    assert result_event_evidence(db_session, task_id) == before
    assert attempt_evidence(db_session) == accepted
    assert db_session.scalar(select(func.count()).select_from(Result)) == 2


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
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261110_0064"


def test_public_worker_cannot_promote_result_to_live(api_client, db_session):
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
    assert evidence["remote_attempts"] == 1
    assert evidence["simulated_attempts"] == evidence["unknown_attempts"] == 0
    assert evidence["completed_attempts"] == 0
    assert evidence["last_attempt_success_at"] is None


def test_http_remote_failure_and_duplicate_keep_remote_provenance(api_client, db_session):
    envelope = running(api_client)
    task_id = UUID(envelope["task"]["id"])
    payload = _result(envelope)
    payload.update(status="failed", error={"code": "timeout", "message": "Synthetic failure"})
    payload["checks"] = [{"type": "execution_profile", "passed": True, "detail": "live"}]
    body = {"lease_id": envelope["lease"]["lease_id"], "result": payload}
    response = api_client.post("/api/v1/worker/result", json=body, headers=_worker())
    assert response.status_code == 200
    stored = db_session.scalar(select(Result).where(Result.task_id == task_id))
    assert stored.execution_profile == "remote"
    assert stored.payload["status"] == "failed"
    assert stored.payload["error"]["code"] == "timeout"
    assert stored.payload["checks"][-1] == {
        "type": "execution_profile",
        "passed": False,
        "detail": "remote",
    }
    event = db_session.scalar(
        select(Event).where(Event.task_id == task_id, Event.event_type == "task.fail")
    )
    assert event.detail["execution_profile"] == "remote"
    assert event.detail["reason"] == "timeout"
    evidence = attempt_evidence(db_session)
    assert evidence["remote_attempts"] == evidence["consecutive_failures"] == 1
    assert evidence["simulated_attempts"] == evidence["unknown_attempts"] == 0
    assert evidence["completed_attempts"] == 0
    assert evidence["last_error_code"] == "timeout" and evidence["last_error_at"] is not None
    assert evidence["last_attempt_at"] is evidence["last_attempt_success_at"] is None
    before = result_event_evidence(db_session, task_id)
    duplicate = api_client.post("/api/v1/worker/result", json=body, headers=_worker())
    assert duplicate.status_code == 422
    assert result_event_evidence(db_session, task_id) == before
    assert attempt_evidence(db_session) == evidence
    assert db_session.scalar(select(func.count()).select_from(Result)) == 1


def test_provenance_migration_classifies_old_results_and_removes_unattested_success(
    api_client, db_session
):
    import json

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
        legacy_constraint = db_session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='lane_status'::regclass AND conname='ck_lane_status_counters'"
            )
        )
        assert "simulated_attempts" not in legacy_constraint
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.execute(
                    text(
                        "INSERT INTO lane_status (lane,status,reason,completed_attempts,"
                        "consecutive_failures) VALUES ('ollama','unavailable','timeout',0,1)"
                    )
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
                "consecutive_failures,last_error_code,last_error_at,"
                "last_attempt_at,last_attempt_success_at,last_success_at) VALUES "
                "('ollama','ok','simulation_ready',7,2,'timeout',clock_timestamp(),"
                "clock_timestamp(),clock_timestamp(),clock_timestamp())"
            )
        )
        legacy = (
            db_session.execute(
                text(
                    "SELECT consecutive_failures,last_error_code,last_error_at,last_success_at "
                    "FROM lane_status WHERE lane='ollama'"
                )
            )
            .mappings()
            .one()
        )
        db_session.commit()
        command.upgrade(config, "head")
        command.check(config)
        result = db_session.scalar(select(Result))
        assert result.execution_profile == "unknown"
        row = db_session.get(LaneStatus, "ollama")
        assert row.unknown_attempts == 7
        assert row.simulated_attempts == row.remote_attempts == row.completed_attempts == 0
        assert row.consecutive_failures == legacy["consecutive_failures"] == 2
        assert row.last_error_code == legacy["last_error_code"] == "timeout"
        assert row.last_error_at == legacy["last_error_at"]
        assert row.last_attempt_success_at is row.last_attempt_at is None
        assert row.last_success_at == legacy["last_success_at"]
        db_session.commit()
        with pytest.raises(RuntimeError, match="execution provenance"):
            command.downgrade(config, "20261104_0017")
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261110_0064"
    finally:
        db_session.rollback()
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "source",
    [
        "result_simulation",
        "result_remote",
        "result_live",
        "simulated_attempts",
        "remote_attempts",
        "unknown_attempts",
        "probe_generation",
        "completed_attempts",
        "consecutive_failures",
        "last_error_code",
        "last_error_at",
        "last_attempt_at",
        "last_attempt_success_at",
    ],
)
def test_0018_downgrade_guards_each_evidence_source(api_client, db_session, source):
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    if source.startswith("result_"):
        envelope = running(api_client)
        db_session.add(
            Result(
                task_id=UUID(envelope["task"]["id"]),
                attempt=1,
                execution_profile=source.removeprefix("result_"),
                payload=_result(envelope),
            )
        )
        assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 0
    else:
        value = (
            datetime(2026, 11, 4, tzinfo=UTC)
            if source.endswith("_at")
            else "timeout"
            if source == "last_error_code"
            else 1
        )
        evidence = {source: value}
        if source == "consecutive_failures":
            # A failure series cannot exist without an attempt under the DB CHECK.
            evidence["completed_attempts"] = 1
        db_session.add(
            LaneStatus(
                lane="ollama", status="unavailable", reason="inventory_unavailable", **evidence
            )
        )
        assert db_session.scalar(select(func.count()).select_from(Result)) == 0
    db_session.commit()
    before = [
        list(db_session.execute(select(model.__table__)).mappings())
        for model in (Result, LaneStatus)
    ]
    db_session.commit()
    try:
        with pytest.raises(RuntimeError, match="execution provenance or accepted attempt evidence"):
            command.downgrade(config, "20261104_0017")
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261110_0064"
        after = [
            list(db_session.execute(select(model.__table__)).mappings())
            for model in (Result, LaneStatus)
        ]
        assert after == before
    finally:
        db_session.rollback()
        command.upgrade(config, "head")


def test_0018_evidence_free_downgrade_and_upgrade_roundtrip(db_session):
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    db_session.commit()
    try:
        command.downgrade(config, "20261104_0017")
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261104_0017"
        inspector = inspect(db_session.get_bind())
        assert "client_hash" in {column["name"] for column in inspector.get_columns("ui_sessions")}
        assert "page_requests" in {
            column["name"] for column in inspector.get_columns("ui_login_clients")
        }
        assert "execution_profile" not in {
            column["name"] for column in inspector.get_columns("results")
        }
        assert not {
            "simulated_attempts",
            "remote_attempts",
            "unknown_attempts",
            "probe_generation",
        } & {column["name"] for column in inspector.get_columns("lane_status")}
        db_session.commit()
        command.upgrade(config, "head")
        command.check(config)
        assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == "20261110_0064"
        assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 0
    finally:
        db_session.rollback()
        command.upgrade(config, "head")


def test_remote_failure_counts_but_only_live_success_resets_series(api_client, db_session):
    assert submit(api_client, running(api_client), error="timeout").status_code == 200
    failed = attempt_evidence(db_session)
    assert failed["completed_attempts"] == 0 and failed["remote_attempts"] == 1
    assert failed["consecutive_failures"] == 1 and failed["last_error_code"] == "timeout"
    assert failed["last_error_at"] is not None
    assert failed["last_attempt_at"] is failed["last_attempt_success_at"] is None
    db_session.commit()

    assert submit(api_client, running(api_client)).status_code == 200
    recovered = attempt_evidence(db_session)
    assert recovered == {**failed, "remote_attempts": 2}
    db_session.commit()

    assert submit_live(db_session, running(api_client)).status_code == 200
    live = attempt_evidence(db_session)
    assert live["completed_attempts"] == 1 and live["remote_attempts"] == 2
    assert live["consecutive_failures"] == 0
    assert live["last_error_code"] == "timeout" and live["last_error_at"] == failed["last_error_at"]
    assert live["last_attempt_success_at"] is not None


@pytest.mark.parametrize("profile", ["simulation", "unknown"])
def test_nonlive_failure_without_live_attempts_is_persisted(api_client, db_session, profile):
    envelope = running(api_client)
    payload = _result(envelope)
    payload.update(status="failed", error={"code": "timeout", "message": "Synthetic failure"})
    with Session(db_session.get_bind()) as writer:
        submit_worker_result(
            writer,
            lease_id=UUID(envelope["lease"]["lease_id"]),
            result_payload=payload,
            execution_profile=profile,
        )
        writer.commit()
    evidence = attempt_evidence(db_session)
    assert evidence["completed_attempts"] == evidence["remote_attempts"] == 0
    assert evidence["simulated_attempts"] == (1 if profile == "simulation" else 0)
    assert evidence["unknown_attempts"] == (1 if profile == "unknown" else 0)
    assert evidence["consecutive_failures"] == 1
    assert evidence["last_error_code"] == "timeout" and evidence["last_error_at"] is not None
    assert evidence["last_attempt_success_at"] is None


@pytest.mark.parametrize(
    "counts",
    [
        {"completed": -1, "simulated": 2, "failures": 1},
        {"completed": 2, "simulated": -1, "failures": 1},
        {"completed": 1, "simulated": 1, "failures": -1},
        {"completed": 1, "simulated": 1, "failures": 3},
    ],
)
def test_attempt_counter_constraint_rejects_impossible_counts(db_session, counts):
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO lane_status (lane, status, reason, completed_attempts, "
                "simulated_attempts, consecutive_failures) "
                "VALUES ('ollama', 'unavailable', 'attempt_recorded', "
                ":completed, :simulated, :failures)"
            ),
            counts,
        )
        db_session.flush()
    db_session.rollback()
