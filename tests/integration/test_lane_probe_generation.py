"""External lane probes retain their age and cannot supersede newer observations."""

from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.audit.health import AttemptHealth, LaneObservation, lane_health_view
from app.core.schemas import Worker
from app.db.models import LaneStatus
from app.workers.lane_status import LaneHealthState, update_lane_status
from app.workers.lanes.ollama import OllamaAvailability, OllamaExecutor
from app.workers.ollama_lane import OllamaLaneRunner
from tests.integration.test_ollama_lane import _write_config


def snapshot(row):
    return {
        name: getattr(row, name)
        for name in ("status", "reason", "probe_generation", "updated_at", "last_success_at")
    }


def observe(db_session, status, reason, *, generation=None):
    # Separate sessions exercise the persisted fence, not a runner's local state.
    with Session(db_session.get_bind()) as writer:
        row = update_lane_status(
            writer,
            lane=Worker.OLLAMA,
            status=status,
            reason=reason,
            probe_generation=generation,
        )
        evidence = snapshot(row)
        writer.commit()
    return evidence


def start_probe(db_session):
    return observe(db_session, LaneHealthState.UNAVAILABLE, "probe_pending")


def health(evidence, *, now):
    return lane_health_view(
        lane=Worker.OLLAMA,
        enabled=True,
        paused=False,
        daily_attempt_limit=None,
        claims_today=0,
        observation=LaneObservation(
            evidence["status"],
            evidence["reason"],
            evidence["updated_at"],
            evidence["last_success_at"],
        ),
        attempts=AttemptHealth(),
        first_claim_at=None,
        now=now,
    )


@pytest.mark.parametrize(
    "older_status,older_reason,newer_status,newer_reason",
    [
        (
            LaneHealthState.OK,
            "live_ready",
            LaneHealthState.UNAVAILABLE,
            "inventory_unavailable",
        ),
        (
            LaneHealthState.UNAVAILABLE,
            "inventory_unavailable",
            LaneHealthState.OK,
            "live_ready",
        ),
    ],
    ids=["old_success_after_new_failure", "old_failure_after_new_success"],
)
def test_older_probe_completion_cannot_overwrite_newer_observation(
    db_session, older_status, older_reason, newer_status, newer_reason
):
    historical = observe(db_session, LaneHealthState.OK, "live_ready")
    older = start_probe(db_session)
    newer = start_probe(db_session)
    assert historical["probe_generation"] < older["probe_generation"] < newer["probe_generation"]
    accepted = observe(
        db_session,
        newer_status,
        newer_reason,
        generation=newer["probe_generation"],
    )
    assert accepted["status"] == newer_status.value and accepted["reason"] == newer_reason
    assert accepted["updated_at"] == newer["updated_at"]
    if newer_status == LaneHealthState.UNAVAILABLE:
        assert accepted["last_success_at"] == historical["last_success_at"]
    else:
        assert accepted["last_success_at"] >= newer["updated_at"]

    rejected = observe(
        db_session,
        older_status,
        older_reason,
        generation=older["probe_generation"],
    )
    assert rejected == accepted
    db_session.expire_all()
    assert snapshot(db_session.get(LaneStatus, "ollama")) == accepted


def test_pause_invalidates_an_in_flight_probe(db_session):
    pending = start_probe(db_session)
    paused = observe(db_session, LaneHealthState.PAUSED, "runner_paused")
    assert paused["probe_generation"] > pending["probe_generation"]
    late = observe(
        db_session,
        LaneHealthState.OK,
        "live_ready",
        generation=pending["probe_generation"],
    )
    assert late == paused
    assert late["last_success_at"] is None
    assert health(late, now=late["updated_at"]).live_available is False


def test_pending_probe_cannot_reuse_previous_live_readiness(db_session):
    ready = observe(db_session, LaneHealthState.OK, "live_ready")
    assert health(ready, now=ready["updated_at"]).live_available is True
    pending = start_probe(db_session)
    assert pending["probe_generation"] > ready["probe_generation"]
    assert pending["last_success_at"] == ready["last_success_at"]
    assert pending["status"] == "unavailable" and pending["reason"] == "probe_pending"
    assert health(pending, now=pending["updated_at"]).live_available is False


def test_probe_completion_retains_start_time_as_freshness_basis(db_session):
    pending = start_probe(db_session)
    aged_start = pending["updated_at"] - timedelta(seconds=31)
    db_session.execute(
        update(LaneStatus).where(LaneStatus.lane == "ollama").values(updated_at=aged_start)
    )
    db_session.commit()
    finished = observe(
        db_session,
        LaneHealthState.OK,
        "live_ready",
        generation=pending["probe_generation"],
    )
    assert finished["probe_generation"] == pending["probe_generation"]
    assert finished["updated_at"] == aged_start
    assert finished["last_success_at"] >= aged_start + timedelta(seconds=31)
    assert health(finished, now=finished["last_success_at"]).live_available is False


def test_probe_is_committed_before_availability_io(db_session, tmp_path, monkeypatch):
    router = tmp_path / "router.yaml"
    _write_config(router, models={"default": "fixture-model"})
    monkeypatch.setenv("DECK_ROUTER_FILE", str(router))
    sessions = []
    observed_generations = []

    def session_factory():
        session = Session(db_session.get_bind(), autoflush=False)
        sessions.append(session)
        return session

    def availability(models):
        assert models
        assert sessions and all(not session.in_transaction() for session in sessions)
        with Session(db_session.get_bind()) as reader:
            row = reader.get(LaneStatus, "ollama")
            assert row.status == "unavailable" and row.reason == "probe_pending"
            assert row.probe_generation > 0
            observed_generations.append(row.probe_generation)
        return OllamaAvailability("unavailable", "inventory_unavailable")

    executor = OllamaExecutor()
    monkeypatch.setattr(executor, "availability", availability)
    runner = OllamaLaneRunner(session_factory, runs_root=tmp_path / "runs", executor=executor)
    outcome = runner.run_once()
    assert outcome.action == "unavailable" and outcome.error_code == "inventory_unavailable"
    assert len(observed_generations) == 1
    row = db_session.get(LaneStatus, "ollama", populate_existing=True)
    assert row.reason == "inventory_unavailable"
    assert row.probe_generation == observed_generations[0]
    assert all(not session.in_transaction() for session in sessions)
