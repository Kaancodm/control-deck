"""Lane health remains safe, atomic and explicit about positive observations."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.schemas import Worker
from app.db.models import LaneStatus
from app.workers.lane_status import (
    LaneHealthError,
    LaneHealthState,
    get_lane_status,
    list_lane_status,
    update_lane_status,
)
from tests.integration.helpers import migration_head


def test_missing_health_is_unknown_without_persisting_defaults(db_session: Session) -> None:
    assert get_lane_status(db_session, lane=Worker.OLLAMA) is None
    assert list_lane_status(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 0


def test_success_and_failure_share_one_row_and_retain_last_success(db_session: Session) -> None:
    first = update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    success_at = first.last_success_at
    assert success_at == first.updated_at
    assert success_at is not None and success_at.tzinfo is not None
    db_session.commit()
    failed = update_lane_status(
        db_session,
        lane=Worker.OLLAMA,
        status=LaneHealthState.UNAVAILABLE,
        reason="model_not_configured",
    )
    assert failed is first
    assert failed.status == "unavailable"
    assert failed.last_success_at == success_at
    assert failed.updated_at >= success_at
    assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 1
    recovered = update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    assert recovered.last_success_at == recovered.updated_at
    assert recovered.last_success_at >= success_at


@pytest.mark.parametrize(
    "state", [LaneHealthState.UNAVAILABLE, LaneHealthState.AUTH_REQUIRED, LaneHealthState.PAUSED]
)
def test_unhealthy_first_observation_does_not_invent_success(
    db_session: Session, state: LaneHealthState
) -> None:
    row = update_lane_status(db_session, lane=Worker.CLAUDE, status=state, reason="not_available")
    assert row.last_success_at is None
    assert row.status == state.value


def test_health_uses_current_database_time_in_longer_transaction(db_session: Session) -> None:
    transaction_start = db_session.scalar(select(func.now()))
    db_session.execute(text("SELECT pg_sleep(0.02)"))
    row = update_lane_status(
        db_session, lane=Worker.CODEX, status=LaneHealthState.OK, reason="simulation_ready"
    )
    assert row.updated_at > transaction_start + timedelta(milliseconds=10)
    assert row.last_success_at == row.updated_at


@pytest.mark.parametrize(
    "lane,status,reason",
    [
        ("ollama", LaneHealthState.OK, "simulation_ready"),
        (Worker.OLLAMA, "ok", "simulation_ready"),
        (Worker.OLLAMA, LaneHealthState.OK, ""),
        (Worker.OLLAMA, LaneHealthState.OK, "Raw configuration value"),
        (Worker.OLLAMA, LaneHealthState.OK, "private/path"),
        (Worker.OLLAMA, LaneHealthState.OK, "code\n"),
        (Worker.OLLAMA, LaneHealthState.OK, "a" * 65),
        (Worker.OLLAMA, LaneHealthState.OK, None),
    ],
)
def test_invalid_health_never_echoes_values_or_partially_updates(
    db_session: Session, lane, status, reason
) -> None:
    row = update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    db_session.commit()
    with pytest.raises(LaneHealthError) as error:
        update_lane_status(db_session, lane=lane, status=status, reason=reason)
    assert str(error.value) == "Lane health metadata is invalid."
    db_session.refresh(row)
    assert row.reason == "simulation_ready" and row.status == "ok"
    assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 1


@pytest.mark.parametrize(
    "lane,status,reason",
    [
        ("other", "ok", "simulation_ready"),
        ("ollama", "unknown", "simulation_ready"),
        ("ollama", "ok", "raw value"),
    ],
)
def test_database_constraints_reject_invalid_observations(
    db_session: Session, lane: str, status: str, reason: str
) -> None:
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(LaneStatus(lane=lane, status=status, reason=reason))
        db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(LaneStatus)) == 0


def test_rollback_discards_health_change(db_session: Session) -> None:
    update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    db_session.rollback()
    assert get_lane_status(db_session, lane=Worker.OLLAMA) is None


def test_listing_is_ordered_and_observations_refresh_cached_identity(db_session: Session) -> None:
    for lane in (Worker.OLLAMA, Worker.CODEX, Worker.CLAUDE):
        update_lane_status(
            db_session, lane=lane, status=LaneHealthState.OK, reason="simulation_ready"
        )
    assert [row.lane for row in list_lane_status(db_session)] == ["claude", "codex", "ollama"]
    retained = get_lane_status(db_session, lane=Worker.OLLAMA)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    with factory() as other:
        update_lane_status(
            other, lane=Worker.OLLAMA, status=LaneHealthState.PAUSED, reason="owner_pause"
        )
        other.commit()
    assert get_lane_status(db_session, lane=Worker.OLLAMA) is retained
    assert retained.status == "paused"


def test_concurrent_updates_keep_one_row_and_preserve_committed_success(
    db_session: Session,
) -> None:
    first = update_lane_status(
        db_session, lane=Worker.OLLAMA, status=LaneHealthState.OK, reason="simulation_ready"
    )
    success_at = first.last_success_at
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    started = Event()

    def fail_observation():
        with factory() as other:
            started.set()
            row = update_lane_status(
                other, lane=Worker.OLLAMA, status=LaneHealthState.UNAVAILABLE, reason="cli_failed"
            )
            captured = (row.updated_at, row.last_success_at)
            other.commit()
            return captured

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fail_observation)
        assert started.wait(2)
        db_session.commit()
        updated_at, retained_success = future.result(timeout=5)
    assert retained_success == success_at
    assert updated_at >= success_at
    rows = list_lane_status(db_session)
    assert len(rows) == 1 and rows[0].status == "unavailable"


def test_lane_health_migration_roundtrip_and_guarded_downgrade(db_session: Session) -> None:
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    db_session.commit()
    command.downgrade(config, "20251103_0009")
    assert "lane_status" not in inspect(db_session.get_bind()).get_table_names()
    command.upgrade(config, "head")
    command.check(config)
    update_lane_status(
        db_session,
        lane=Worker.OLLAMA,
        status=LaneHealthState.UNAVAILABLE,
        reason="live_profile_unavailable",
    )
    db_session.commit()
    with pytest.raises(RuntimeError, match="lane health observations would be lost"):
        command.downgrade(config, "20251103_0009")
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == migration_head()
    assert get_lane_status(db_session, lane=Worker.OLLAMA).reason == "live_profile_unavailable"
