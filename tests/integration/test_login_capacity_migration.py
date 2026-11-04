"""Login-page protection survives rejected downgrades and legacy challenge migration."""

import argparse

import pytest
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import func, select, text

from alembic import command
from app.db.ui_auth import UiLoginClient, UiSession, create_anonymous_session, limit_login_page
from app.ui.auth import AuthSettings, client_fingerprint, token_digest
from tests.integration.helpers import migration_head


@pytest.fixture
def settings():
    return AuthSettings(password_hash=PasswordHasher().hash("synthetic-migration-password"))


def configuration():
    config = Config("alembic.ini")
    config.cmd_opts = argparse.Namespace(x=["target=test"])
    return config


def test_active_get_budget_blocks_downgrade_without_losing_counter(db_session, settings):
    limit_login_page(db_session, settings, client="synthetic-client")
    db_session.commit()
    with pytest.raises(RuntimeError, match="active login-page request windows"):
        command.downgrade(configuration(), "20251103_0016")
    assert db_session.scalar(select(UiLoginClient.page_requests)) == 1
    assert db_session.scalar(text("SELECT version_num FROM alembic_version")) == migration_head()


def test_upgrade_groups_legacy_anonymous_sessions_without_losing_challenges(db_session, settings):
    challenge = create_anonymous_session(db_session, settings, client="synthetic-client")
    db_session.commit()
    config = configuration()
    try:
        command.downgrade(config, "20251103_0016")
        command.upgrade(config, "head")
        command.check(config)
        row = db_session.get(UiSession, token_digest(challenge.token))
        assert row.client_hash == client_fingerprint(None)
        assert row.csrf_hash == token_digest(challenge.csrf_token)
        assert db_session.scalar(select(func.count()).select_from(UiSession)) == 1
    finally:
        db_session.rollback()
        command.upgrade(config, "head")
