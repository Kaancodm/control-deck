"""Owner HTTP flows enforce persisted authentication and ordered global revocation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html.parser import HTMLParser
from threading import Event

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.deps import get_db
from app.db.ui_auth import AuthEvent, UiLoginState, UiSession, unlock_owner_login
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE, token_digest

PASSWORD = "synthetic-http-hardening-password"


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name"):
            self.values[values["name"]] = values.get("value", "")


@pytest.fixture(scope="module")
def password_hash():
    return PasswordHasher().hash(PASSWORD)


@pytest.fixture
def ui_factory(db_session, monkeypatch, password_hash):
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)

    def client():
        app = create_app()

        def database():
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        app.dependency_overrides[get_db] = database
        return TestClient(app)

    return client, factory


def login(client, *, origin=None):
    response = client.get("/ui/login")
    assert response.status_code == 200
    csrf = Inputs(response.text).values["csrf"]
    response = client.post(
        "/ui/login",
        data={"csrf": csrf, "password": PASSWORD},
        headers={"Origin": origin} if origin else {},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client.cookies[CSRF_COOKIE]


def test_global_logout_revokes_all_browsers_and_audits_once(ui_factory):
    make_client, factory = ui_factory
    with make_client() as first, make_client() as second:
        csrf = login(first)
        login(second)
        assert "Ueberall abmelden" in first.get("/ui/tasks").text
        stale = dict(first.cookies)
        response = first.post("/ui/logout-all", data={"csrf": csrf}, follow_redirects=False)
        assert response.status_code == 303
        assert SESSION_COOKIE not in first.cookies and CSRF_COOKIE not in first.cookies
        assert second.get("/ui/tasks", follow_redirects=False).status_code == 303
        first.cookies.update(stale)
        assert first.post("/ui/logout-all", data={"csrf": csrf}).status_code == 403
    with factory() as session:
        rows = session.scalars(
            select(AuthEvent).where(AuthEvent.event_type == "auth.sessions_revoked")
        ).all()
        assert len(rows) == 1 and rows[0].affected_sessions == 2
        assert not session.scalar(
            select(func.count()).select_from(UiSession).where(UiSession.revoked_at.is_(None))
        )


@pytest.mark.parametrize("csrf_value", ["", "forged"])
def test_global_logout_requires_csrf_without_revocation(ui_factory, csrf_value):
    make_client, factory = ui_factory
    with make_client() as client:
        login(client)
        response = client.post("/ui/logout-all", data={"csrf": csrf_value})
        assert response.status_code == 403
        assert client.get("/ui/tasks").status_code == 200
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthEvent)
                .where(AuthEvent.event_type == "auth.sessions_revoked")
            )
            == 0
        )


def test_anonymous_session_cannot_logout_everywhere(ui_factory):
    make_client, _ = ui_factory
    with make_client() as client:
        response = client.get("/ui/login")
        csrf = Inputs(response.text).values["csrf"]
        assert client.post("/ui/logout-all", data={"csrf": csrf}).status_code == 403


def test_global_logout_rejects_cross_origin_even_with_csrf(ui_factory):
    make_client, _ = ui_factory
    with make_client() as client:
        csrf = login(client)
        response = client.post(
            "/ui/logout-all", data={"csrf": csrf}, headers={"Origin": "https://foreign.invalid"}
        )
        assert response.status_code == 403
        assert client.get("/ui/tasks").status_code == 200


def test_configured_public_origin_works_without_trusting_forwarding_headers(
    ui_factory, monkeypatch
):
    monkeypatch.setenv("DECK_PUBLIC_ORIGIN", "https://testserver")
    make_client, _ = ui_factory
    with make_client() as client:
        csrf = login(client, origin="https://testserver")
        response = client.post(
            "/ui/logout-all",
            data={"csrf": csrf},
            headers={"Origin": "http://testserver", "X-Forwarded-Proto": "http"},
        )
        assert response.status_code == 403
        assert client.get("/ui/tasks").status_code == 200
        response = client.post(
            "/ui/logout-all",
            data={"csrf": csrf},
            headers={"Origin": "https://testserver", "X-Forwarded-Host": "foreign.invalid"},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_locked_login_stays_visible_after_app_restart_until_local_unlock(ui_factory):
    make_client, factory = ui_factory
    with make_client() as client:
        response = client.get("/ui/login")
        csrf = Inputs(response.text).values["csrf"]
        with factory.begin() as session:
            session.execute(update(UiLoginState).values(daily_failures=19))
        response = client.post("/ui/login", data={"csrf": csrf, "password": "synthetic-wrong"})
        assert response.status_code == 423
        assert "Anmeldung gesperrt" in response.text
        assert "synthetic-wrong" not in response.text
        assert "make unlock-login" in response.text
        assert "Retry-After" not in response.headers
    with make_client() as restarted:
        response = restarted.get("/ui/login")
        assert response.status_code == 423
        assert "Anmeldung gesperrt" in response.text
        assert '<input id="password"' not in response.text
        with factory.begin() as session:
            unlock_owner_login(session)
        login(restarted)
        assert restarted.get("/ui/tasks").status_code == 200
    with factory() as session:
        types = session.scalars(select(AuthEvent.event_type)).all()
        assert types.count("auth.login_locked") == 1
        assert types.count("auth.login_unlocked") == 1


@pytest.mark.parametrize(
    "column,age", [("last_active_at", timedelta(minutes=30)), ("expires_at", timedelta())]
)
def test_expired_browser_session_is_rejected_without_revival(ui_factory, column, age):
    make_client, factory = ui_factory
    with make_client() as client:
        csrf = login(client)
        digest = token_digest(client.cookies[SESSION_COOKIE])
        with factory.begin() as session:
            session.execute(
                update(UiSession)
                .where(UiSession.token_hash == digest)
                .values(**{column: func.clock_timestamp() - age})
            )
        assert client.get("/ui/tasks", follow_redirects=False).status_code == 303
        assert client.post("/ui/logout-all", data={"csrf": csrf}).status_code == 403
        assert client.get("/ui/tasks", follow_redirects=False).status_code == 303


def test_password_change_rejects_existing_browser_session(ui_factory, monkeypatch):
    make_client, _ = ui_factory
    with make_client() as client:
        csrf = login(client)
        monkeypatch.setenv(
            "DECK_OWNER_PASSWORD_HASH", PasswordHasher().hash("synthetic-new-password")
        )
        assert client.get("/ui/tasks", follow_redirects=False).status_code == 303
        assert client.post("/ui/logout-all", data={"csrf": csrf}).status_code == 403


def test_logout_waits_for_login_state_without_holding_a_session_lock(ui_factory):
    make_client, factory = ui_factory
    with make_client() as client:
        csrf = login(client)
        digest = token_digest(client.cookies[SESSION_COOKIE])
        reached_login_lock = Event()
        engine = factory.kw["bind"]

        def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
            if "FROM ui_login_state" in statement and "FOR UPDATE" in statement:
                reached_login_lock.set()

        with factory() as blocker:
            blocker.execute(select(UiLoginState).with_for_update()).all()
            event.listen(engine, "before_cursor_execute", before_cursor_execute)
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(
                        client.post,
                        "/ui/logout-all",
                        data={"csrf": csrf},
                        follow_redirects=False,
                    )
                    try:
                        assert reached_login_lock.wait(5)
                        with factory.begin() as observer:
                            observer.execute(text("SET LOCAL lock_timeout = '1s'"))
                            assert (
                                observer.scalar(
                                    select(UiSession)
                                    .where(UiSession.token_hash == digest)
                                    .with_for_update(nowait=True)
                                )
                                is not None
                            )
                    finally:
                        blocker.rollback()
                    assert pending.result(timeout=10).status_code == 303
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor_execute)
