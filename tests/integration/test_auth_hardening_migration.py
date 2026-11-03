"""Upgrade invalidates unbound legacy sessions; downgrade preserves security state."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.db.ui_auth import AuthEvent, unlock_owner_login


def config() -> Config:
    value = Config(str(Path("alembic.ini")))
    value.cmd_opts = argparse.Namespace(x=["target=test"])
    return value


def test_upgrade_revokes_legacy_owner_sessions(db_session) -> None:
    db_session.commit()
    command.downgrade(config(), "20251103_0013")
    try:
        db_session.execute(
            text(
                "INSERT INTO ui_sessions(token_hash,csrf_hash,authenticated,created_at,expires_at) "
                "VALUES (:token,:csrf,true,clock_timestamp(),clock_timestamp()+interval '1 hour')"
            ),
            {"token": "a" * 64, "csrf": "b" * 64},
        )
        db_session.commit()
        command.upgrade(config(), "head")
        row = db_session.execute(
            text("SELECT revoked_at, last_active_at, password_fingerprint FROM ui_sessions")
        ).one()
        assert row.revoked_at is not None
        assert row.last_active_at is not None
        assert row.password_fingerprint is None
        db_session.rollback()
        command.check(config())
    finally:
        db_session.rollback()
        command.upgrade(config(), "head")


def test_downgrade_cannot_erase_auth_audit(db_session) -> None:
    unlock_owner_login(db_session)
    db_session.commit()
    with pytest.raises(RuntimeError, match="authentication audit"):
        command.downgrade(config(), "20251103_0013")
    assert db_session.query(AuthEvent).count() == 1


def test_downgrade_cannot_remove_active_global_lock(db_session) -> None:
    unlock_owner_login(db_session)
    db_session.flush()
    db_session.execute(text("DELETE FROM auth_events"))
    db_session.execute(text("UPDATE ui_login_state SET locked_at=clock_timestamp()"))
    db_session.commit()
    with pytest.raises(RuntimeError, match="protection state"):
        command.downgrade(config(), "20251103_0013")
