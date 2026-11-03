"""Real Chromium smoke tests against an isolated app, PostgreSQL and fake executors."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest
import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.deps import get_db
from app.core.schemas import Worker
from app.db.models import Task
from app.db.state_machine import TaskState
from app.db.ui_auth import UiLoginState, unlock_owner_login
from app.ui.chat import SimulatedChat
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor


@pytest.fixture
def browser_app(db_session, monkeypatch, tmp_path):
    password = "synthetic-browser-owner-password"
    monkeypatch.setenv("DECK_OWNER_PASSWORD_HASH", PasswordHasher().hash(password))
    monkeypatch.setenv("DECK_UI_COOKIE_SECURE", "false")
    monkeypatch.setenv("DECK_INBOX_DIR", str(tmp_path / "inbox"))
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    app = create_app()
    app.state.local_chat = SimulatedChat()

    def database():
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = database
    listener = socket.socket()
    listener.bind(("localhost", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= end:
            raise AssertionError("Isolated browser app did not start")
        time.sleep(0.01)
    try:
        yield f"http://localhost:{port}", password, factory
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("width,javascript", [(390, True), (1024, False)])
def test_owner_browser_flow_at_phone_and_desktop_widths(browser_app, tmp_path, width, javascript):
    base, password, factory = browser_app
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=shutil.which("chromium"),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": width, "height": 844}, java_script_enabled=javascript
        )
        page = context.new_page()
        external = []
        errors = []

        def allowed(route):
            if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", allowed)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/ui/tasks")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        assert page.url.endswith("/ui/tasks"), (
            page.url,
            page.locator("main").inner_text(),
            [item["name"] for item in context.cookies()],
        )
        page.get_by_role("link", name="＋ Neuer Auftrag", exact=True).click()
        page.get_by_label("Titel", exact=True).fill("Browser smoke task")
        page.get_by_label("Anweisung", exact=True).fill("Produce synthetic text.")
        page.get_by_label("Dateien: UTF-8-Text, Markdown, JSON oder CSV").set_input_files(
            {"name": "smoke.md", "mimeType": "text/markdown", "buffer": b"Synthetic attachment."}
        )
        page.get_by_role("button", name="Auftrag zur Start-Freigabe speichern").click()
        page.get_by_label("Auftrag und Datenklasse geprueft").check()
        page.get_by_role("button", name="Start freigeben", exact=True).click()
        with factory() as session:
            task = session.scalar(select(Task))
            assert task.status == TaskState.QUEUED and task.approved_at is not None
        outcome = HostRunner(factory, runs_root=tmp_path / "runs").run_once(
            Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA)
        )
        assert outcome.action == "completed"
        page.reload()
        page.get_by_text("simulation-ok", exact=True).wait_for()
        page.get_by_label("Ergebnis geprueft", exact=True).check()
        page.get_by_role("button", name="Ergebnis freigeben", exact=True).click()
        page.get_by_text("✓ Erledigt", exact=True).wait_for()
        with factory() as session:
            assert session.scalar(select(Task)).status == TaskState.DONE
        page.wait_for_load_state("load")
        page.screenshot(path=str(tmp_path / f"owner-flow-{width}.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # Changing a central theme token does not change the task flow or markup.
        page.evaluate("document.documentElement.style.setProperty('--color-bg', 'rgb(9, 20, 35)')")
        assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(9, 20, 35)"
        for path in ("/ui/tasks", "/ui/failed", "/ui/lanes", "/ui/chat", "/ui/tasks/new"):
            page.goto(base + path)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
            assert page.get_by_role("heading", level=1).count() == 1
        page.goto(base + "/ui/chat")
        page.get_by_label("Nachricht", exact=True).fill("DRAFT Produce synthetic text.")
        page.get_by_role("button", name="Senden", exact=True).click()
        page.get_by_role("button", name="Entwurf im Auftragsformular pruefen").click()
        assert page.get_by_label("Titel", exact=True).input_value() == "Simulierter Entwurf"
        with factory() as session:
            assert len(session.scalars(select(Task)).all()) == 1
        page.get_by_role("button", name="Abmelden", exact=True).click()
        page.wait_for_url("**/ui/login")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/tasks")
        second_context = browser.new_context(java_script_enabled=javascript)
        second = second_context.new_page()
        second.route("**/*", allowed)
        second.goto(base + "/ui/login")
        second.get_by_label("Passwort", exact=True).fill(password)
        second.get_by_role("button", name="Anmelden", exact=True).click()
        second.wait_for_url("**/ui/tasks")
        page.get_by_role("button", name="Ueberall abmelden", exact=True).click()
        page.wait_for_url("**/ui/login")
        second.reload()
        second.wait_for_url("**/ui/login")
        with factory.begin() as session:
            session.execute(update(UiLoginState).values(daily_failures=19))
        page.get_by_label("Passwort", exact=True).fill("synthetic-wrong-password")
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.get_by_text("Anmeldung gesperrt", exact=True).wait_for()
        page.reload()
        assert page.get_by_text("Anmeldung gesperrt", exact=True).is_visible()
        assert page.get_by_label("Passwort", exact=True).count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        with factory.begin() as session:
            unlock_owner_login(session)
        page.reload()
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/tasks")
        assert not external and not errors
        second_context.close()
        context.close()
        browser.close()
