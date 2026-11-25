"""Quarantine inbox at phone width with JavaScript and desktop width without it."""

from __future__ import annotations

import shutil
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.memory.ingest import ingest_turn
from tests.integration.test_ui_browser import browser_app as browser_app

TERM = "zucchiniplan"
BODY = f"Note about {TERM} today."


@pytest.mark.parametrize("width,javascript", [(390, True), (1024, False)])
def test_marked_quarantine_approves_without_horizontal_scroll(
    browser_app, monkeypatch, tmp_path, width, javascript
) -> None:
    path = tmp_path / "blocklist.txt"
    path.write_text(f"{TERM}\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    base, password, factory = browser_app
    with factory() as session:
        outcome = ingest_turn(
            session,
            {
                "schema_version": "channel_turn.v1",
                "channel": "deck_ui",
                "account": "owner",
                "external_thread_id": "browser-memory-1",
                "external_message_id": f"m-{uuid.uuid4().hex}",
                "role": "owner",
                "text": BODY,
                "occurred_at": "2026-11-10T08:00:00Z",
                "data_class": "cloud_ok",
                "ingest_method": "deck_ui",
                "source_ref": {"ref": "deck_ui:browser-memory-1"},
            },
        )
        session.commit()
    assert outcome.privacy_hold == "privacy_block"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=shutil.which("chromium"),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": width, "height": 844},
            java_script_enabled=javascript,
        )
        page = context.new_page()
        external: list[str] = []

        def local_only(route):
            if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", local_only)
        page.goto(base + "/ui/login")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/overview")
        page.goto(base + "/ui/memory")
        page.wait_for_load_state("load")
        assert BODY in page.content()
        assert "Datenschutz-Treffer" in page.content()
        assert "Markierte Quarantäne" in page.content()
        page.get_by_role("button", name="Freigeben", exact=True).click()
        page.wait_for_load_state("load")
        quarantine = page.locator("#quarantaene").inner_text()
        assert BODY not in quarantine
        assert "Keine Zeile in der Quarantäne." in quarantine
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not external
        browser.close()
