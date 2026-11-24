"""Emulated browser smoke for the assistant. This is not a physical iPhone or Safari."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest
import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.deps import get_db
from app.ui.chat import SimulatedChat


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
        yield f"http://localhost:{port}", password
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("width,javascript", [(320, True), (390, False), (1024, True)])
def test_emulated_assistant_layout(browser_app, width, javascript):
    chromium = shutil.which("chromium")
    if chromium is None:
        pytest.skip("chromium emulator is not installed")
    base, password = browser_app
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=chromium,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": width, "height": 844}, java_script_enabled=javascript
        )
        page = context.new_page()
        external: list[str] = []

        def allowed(route):
            if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", allowed)
        page.goto(base + "/ui/assistant")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.goto(base + "/ui/assistant")
        page.get_by_label("Nachricht", exact=True).fill("Wie geht es dir heute Morgen")
        button = page.get_by_role("button", name="Dialog senden", exact=True)
        box = button.bounding_box()
        assert box is not None and box["height"] >= 44
        fits = page.locator(".assistant-page").evaluate(
            "node => node.scrollWidth <= node.clientWidth + 1"
        )
        assert fits
        button.click()
        main = page.locator("main")
        expect(main).to_contain_text("kein Live-Modell", timeout=20_000)
        if javascript:
            expect(main).to_contain_text("Nicht gespeichert", timeout=20_000)
            expect(main).to_contain_text("nicht gebaut", timeout=20_000)
        else:
            expect(main).to_contain_text("Nicht gespeichert", timeout=5_000)
            expect(main).to_contain_text("nicht gebaut", timeout=5_000)
        assert external == []
        browser.close()


def test_webkit_emulator_is_optional_and_not_a_phone(browser_app):
    base, password = browser_app
    with sync_playwright() as playwright:
        try:
            browser = playwright.webkit.launch(headless=True)
        except Exception:
            pytest.skip("WebKit emulator is not installed; this is not a physical iPhone result")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base + "/ui/login")
        assert page.get_by_label("Passwort", exact=True).is_visible()
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.goto(base + "/ui/assistant")
        assert "kein Live-Modell" in page.locator("main").inner_text()
        browser.close()
