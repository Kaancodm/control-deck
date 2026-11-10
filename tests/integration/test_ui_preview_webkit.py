"""WebKit preview screenshots at 390 x 844 (plan section 9, variant A).

Emulation only; this is not a physical iPhone or Safari result. Screenshots go to
``DECK_UI_PREVIEW_DIR`` (default: the pytest tmp_path) and are never committed.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tests.integration.test_ui_browser import browser_app as browser_app


def test_webkit_preview_screenshots(browser_app, tmp_path):
    base, password, _ = browser_app
    target = Path(os.environ.get("DECK_UI_PREVIEW_DIR") or tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.webkit.launch(headless=True)
        except PlaywrightError as exc:
            # Only a missing browser binary is a skip; any other start failure must fail loudly.
            if "Executable doesn't exist" not in str(exc):
                raise
            pytest.skip("WebKit emulator is not installed; this is not a physical iPhone result")
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
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
        fixed = page.evaluate(
            "Array.from(document.querySelectorAll('*'))"
            ".filter(e => ['fixed', 'sticky'].includes(getComputedStyle(e).position)).length"
        )
        assert fixed == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(target / "login-390x844.png"), full_page=True)
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/overview")
        for name, path in (("overview", "/ui/overview"), ("assistant", "/ui/assistant")):
            page.goto(base + path)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
            assert page.locator(".tabbar").is_visible()
            page.screenshot(path=str(target / f"{name}-390x844.png"), full_page=True)
        assert external == []
        context.close()
        browser.close()
