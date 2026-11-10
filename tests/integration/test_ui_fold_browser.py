"""Chromium fold, menu and tab-bar checks at phone size (review round 1, Fable B7).

Emulation only; this is not a physical iPhone or Safari result. Thresholds are the
review targets for an empty deck at 390 x 844 and are not loosened here.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from tests.integration.test_ui_browser import browser_app as browser_app
from tests.integration.test_ui_shell_browser import _launch, _local_only

# Filled (primary) buttons that are rendered and visible inside main.
FILLED = (
    "Array.from(document.querySelectorAll('main button, main a.button'))"
    ".filter(e => !e.matches('.quiet, .ghost, .danger'))"
    ".filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
    ".map(e => e.textContent.trim())"
)
# The element itself (or a child of it) is the topmost hit at its centre.
ON_TOP = (
    "selector => { const el = document.querySelector(selector);"
    " const r = el.getBoundingClientRect();"
    " const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);"
    " return hit === el || el.contains(hit); }"
)
SCROLL_HEIGHT = "document.documentElement.scrollHeight"


def _login(page, base, password):
    page.goto(base + "/ui/login")
    page.get_by_label("Passwort", exact=True).fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url("**/ui/overview")


def _bottom(box):
    return box["y"] + box["height"]


def test_overview_and_assistant_fit_the_phone_fold(browser_app):
    base, password, _ = browser_app
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        external: list[str] = []
        page.route("**/*", _local_only(base, external))
        _login(page, base, password)

        page.goto(base + "/ui/overview")
        assert page.evaluate("window.scrollY") == 0
        header = page.locator(".topbar").bounding_box()
        assert header is not None and header["height"] <= 64
        h1 = page.locator("main h1").bounding_box()
        assert h1 is not None and h1["y"] <= 150
        assert page.evaluate(FILLED) == ["＋ Neuer Auftrag"]  # one filled action per screen
        primary = page.get_by_role("link", name="＋ Neuer Auftrag", exact=True).bounding_box()
        assert primary is not None and _bottom(primary) <= 260
        status = page.locator(".kpis").bounding_box()
        assert status is not None and _bottom(status) <= 430
        open_points = page.locator("#offen").bounding_box()
        assert open_points is not None and open_points["y"] <= 450
        assert page.evaluate(SCROLL_HEIGHT) <= 2000
        assert not page.locator(".hero").is_visible()
        for anchor in ("assistent", "meldungen", "audit"):
            assert page.locator(f"details#{anchor}").get_attribute("open") is None
            summary = page.locator(f"details#{anchor} > summary").bounding_box()
            assert summary is not None and summary["height"] >= 43.5

        page.goto(base + "/ui/assistant")
        message = page.locator("#message").bounding_box()
        assert message is not None and message["y"] <= 380
        send = page.get_by_role("button", name="Dialog senden", exact=True).bounding_box()
        assert send is not None and _bottom(send) <= 740
        assert page.evaluate(SCROLL_HEIGHT) <= 2200
        assert page.evaluate(FILLED) == ["Dialog senden"]
        assert "kein Live-Modell" in page.locator("main").inner_text()

        assert external == []
        context.close()
        browser.close()


@pytest.mark.parametrize("path", ["/ui/overview", "/ui/lanes"])
def test_open_menu_lies_above_the_page_on_a_phone(browser_app, path):
    base, password, _ = browser_app
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        external: list[str] = []
        page.route("**/*", _local_only(base, external))
        _login(page, base, password)
        page.goto(base + path)
        page.get_by_text("Menü", exact=True).click()
        assert page.locator(".menu-panel").is_visible()
        assert page.evaluate(ON_TOP, ".menu-nav a:last-child"), path
        assert page.evaluate(ON_TOP, ".menu-panel form button"), path
        last = page.locator(".menu-nav a:last-child")
        assert last.inner_text().strip() == "Audit"
        assert external == []
        context.close()
        browser.close()


def test_tab_bar_is_opaque_and_yields_to_the_keyboard(browser_app):
    base, password, _ = browser_app
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        # A short viewport stands in for the on-screen keyboard.
        context = browser.new_context(viewport={"width": 390, "height": 340})
        page = context.new_page()
        external: list[str] = []
        page.route("**/*", _local_only(base, external))
        _login(page, base, password)
        page.goto(base + "/ui/assistant")
        bar = page.locator(".tabbar")
        assert bar.is_visible()
        style = page.evaluate(
            "() => { const s = getComputedStyle(document.querySelector('.tabbar'));"
            " return [s.backgroundColor, s.backdropFilter || 'none']; }"
        )
        assert style[0].startswith("rgb(") and style[1] == "none", style  # no alpha, no blur
        field = page.get_by_label("Nachricht", exact=True)
        field.focus()
        assert not bar.is_visible()
        box = field.bounding_box()
        assert box is not None and box["y"] >= 0 and _bottom(box) <= 340
        page.evaluate("document.activeElement.blur()")
        assert bar.is_visible()
        assert external == []
        context.close()
        browser.close()
