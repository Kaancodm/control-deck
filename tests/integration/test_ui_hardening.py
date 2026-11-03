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
from app.db.ui_auth import (
    AuthEvent,
    UiLoginClient,
    UiLoginState,
    UiSession,
    release_owner_login_lock_on_startup,
    unlock_owner_login,
)
from app.ui.auth import CSRF_COOKIE, SESSION_COOKIE, token_digest

PASSWORD = "synthetic-http-hardening-password"
WRONG_PASSWORD = "synthetic-wrong"


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

    def client(peer="testclient"):
        """``peer`` is the ASGI connection address seen by the app, never a header value."""
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
        return TestClient(app, client=(peer, 50000))

    return client, factory


def fail_login(client, csrf, headers=None):
    return client.post(
        "/ui/login", data={"csrf": csrf, "password": WRONG_PASSWORD}, headers=headers or {}
    )


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
        assert "Überall abmelden" in first.get("/ui/tasks").text
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


def test_locked_login_shows_423_until_local_unlock(ui_factory):
    make_client, factory = ui_factory
    with make_client() as client:
        response = client.get("/ui/login")
        csrf = Inputs(response.text).values["csrf"]
        with factory.begin() as session:
            session.execute(update(UiLoginState).values(daily_failures=19))
        response = fail_login(client, csrf)
        assert response.status_code == 423
        assert "Anmeldung gesperrt" in response.text
        assert WRONG_PASSWORD not in response.text
        assert "make unlock-login" in response.text
        assert "Retry-After" not in response.headers
    with make_client("synthetic-client-b") as other:
        response = other.get("/ui/login")
        assert response.status_code == 423
        assert "Anmeldung gesperrt" in response.text
        assert '<input id="password"' not in response.text
        with factory.begin() as session:
            unlock_owner_login(session)
        login(other)
        assert other.get("/ui/tasks").status_code == 200
    with factory() as session:
        types = session.scalars(select(AuthEvent.event_type)).all()
        assert types.count("auth.login_locked") == 1
        assert types.count("auth.login_unlocked") == 1


def test_restart_clears_global_lock(ui_factory):
    make_client, factory = ui_factory
    with make_client() as client:
        csrf = Inputs(client.get("/ui/login").text).values["csrf"]
        with factory.begin() as session:
            session.execute(update(UiLoginState).values(daily_failures=19))
        assert fail_login(client, csrf).status_code == 423
        assert client.get("/ui/login").status_code == 423
    # The launcher runs this startup path before it opens the listener.
    with factory.begin() as session:
        assert release_owner_login_lock_on_startup(session)
    with make_client() as restarted:
        response = restarted.get("/ui/login")
        assert response.status_code == 200
        assert '<input id="password"' in response.text
        login(restarted)
        assert restarted.get("/ui/tasks").status_code == 200
    with factory() as session:
        types = session.scalars(select(AuthEvent.event_type)).all()
        assert types.count("auth.login_locked") == 1
        assert types.count("auth.login_unlocked") == 1


def test_throttle_is_per_client(ui_factory):
    make_client, factory = ui_factory
    with make_client("synthetic-client-a") as first, make_client("synthetic-client-b") as second:
        csrf = Inputs(first.get("/ui/login").text).values["csrf"]
        for _ in range(5):
            assert fail_login(first, csrf).status_code == 401
        response = first.post("/ui/login", data={"csrf": csrf, "password": PASSWORD})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert "synthetic-client" not in response.text
        assert first.get("/ui/login").status_code == 200
        login(second)
        assert second.get("/ui/tasks").status_code == 200
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UiLoginClient)) == 2
        assert session.scalar(select(UiLoginState.daily_failures)) == 5
        assert session.scalar(select(UiLoginState.locked_at)) is None
        types = session.scalars(select(AuthEvent.event_type)).all()
        assert types.count("auth.login_throttled") == 1
        assert types.count("auth.login_succeeded") == 1


def test_forwarded_header_does_not_change_client_identity(ui_factory):
    make_client, factory = ui_factory
    with make_client("synthetic-client-a") as client:
        csrf = Inputs(client.get("/ui/login").text).values["csrf"]
        for _ in range(5):
            assert fail_login(client, csrf).status_code == 401
        forged = {"X-Forwarded-For": "synthetic-client-b", "Forwarded": "for=synthetic-client-b"}
        response = client.post(
            "/ui/login", data={"csrf": csrf, "password": PASSWORD}, headers=forged
        )
        assert response.status_code == 429
        response = fail_login(client, csrf, headers={"X-Real-IP": "synthetic-client-c"})
        assert response.status_code == 429
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UiLoginClient)) == 1
        assert session.scalar(select(UiLoginState.daily_failures)) == 5


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
