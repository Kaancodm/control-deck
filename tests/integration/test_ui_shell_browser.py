"""Chromium smoke for the command-center shell at phone and desktop widths.

Emulation only; this is not a physical iPhone or Safari result.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from tests.integration.test_ui_browser import browser_app as browser_app

NO_HORIZONTAL_SCROLL = "document.documentElement.scrollWidth <= window.innerWidth"
FIXED_OR_STICKY = (
    "Array.from(document.querySelectorAll('*'))"
    ".filter(e => ['fixed', 'sticky'].includes(getComputedStyle(e).position))"
    ".map(e => e.tagName + '.' + e.className)"
)
UNCOVERED = (
    "selector => { const el = document.querySelector(selector);"
    " const r = el.getBoundingClientRect();"
    " return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === el; }"
)
# Visible controls below 44 px. Inline links inside notices and hints are exempt.
SMALL_CONTROLS = (
    "Array.from(document.querySelectorAll('a, button, summary'))"
    ".filter(e => !e.closest('.notice, .hint, .skip-link') && !e.classList.contains('skip-link'))"
    ".filter(e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);"
    " return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; })"
    ".filter(e => e.getBoundingClientRect().height < 43.5)"
    ".map(e => [e.tagName, e.textContent.trim(), e.getBoundingClientRect().height])"
)
CLIPPED_TABS = (
    "Array.from(document.querySelectorAll('.tabbar a span'))"
    ".filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)"
)


def _launch(playwright):
    try:
        return playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    except Exception:
        pytest.skip("Chromium emulator is not installed; this is not a physical phone result")


def _local_only(base, external):
    def handler(route):
        if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    return handler


@pytest.mark.parametrize("width", [320, 390, 1024])
def test_shell_layout_navigation_focus_and_login(browser_app, width):
    base, password, _ = browser_app
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": width, "height": 844})
        page = context.new_page()
        external: list[str] = []
        errors: list[str] = []
        page.route("**/*", _local_only(base, external))
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.goto(base + "/")
        page.wait_for_url("**/ui/login")
        # Nothing fixed or sticky sits over the login fields; no navigation for guests.
        assert page.evaluate(FIXED_OR_STICKY) == []
        assert page.evaluate(UNCOVERED, "#password")
        assert page.locator(".tabbar, .sidebar").count() == 0
        assert page.evaluate(NO_HORIZONTAL_SCROLL)
        assert page.get_by_role("heading", level=1).count() == 1
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/overview")

        phone = width <= 860
        for path in ("/ui/overview", "/ui/assistant"):
            page.goto(base + path)
            assert page.evaluate(NO_HORIZONTAL_SCROLL), path
            assert page.get_by_role("heading", level=1).count() == 1, path
            assert page.locator("main").count() == 1
            assert page.locator(".tabbar").is_visible() is phone, path
            assert page.locator(".sidebar").is_visible() is not phone, path
            assert page.evaluate(SMALL_CONTROLS) == [], path
            assert page.get_by_role("button", name="Abmelden", exact=True).is_visible()
            if phone:
                assert page.evaluate(CLIPPED_TABS) == [], path
                labels = page.locator(".tabbar a").all_inner_texts()
                assert [label.strip() for label in labels] == [
                    "Übersicht",
                    "Lanes",
                    "Assistent",
                    "Meldungen",
                    "Offen",
                ]

        # Keyboard focus on a navigation entry shows a visible outline.
        target = page.locator(".tabbar a" if phone else ".side-nav a").nth(1)
        target.focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        outline = page.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return [s.outlineStyle, parseFloat(s.outlineWidth)]; }"
        )
        assert outline[0] != "none" and outline[1] >= 2

        if phone:
            # After focusing the message field the tab bar does not cover it.
            field = page.get_by_label("Nachricht", exact=True)
            field.scroll_into_view_if_needed()
            field.focus()
            message = field.bounding_box()
            bar = page.locator(".tabbar").bounding_box()
            assert message is not None and bar is not None
            assert (
                message["y"] + message["height"] <= bar["y"]
                or message["y"] >= bar["y"] + bar["height"]
            )
            # Anchors from the tab bar land on real sections of the overview.
            page.goto(base + "/ui/overview")
            page.locator(".tabbar").get_by_role("link", name="Offen", exact=True).click()
            assert page.url.endswith("/ui/overview#offen")
            assert page.locator("#offen").is_visible()

        assert external == [] and errors == []
        context.close()
        browser.close()


def test_reduced_motion_disables_transitions(browser_app):
    base, password, _ = browser_app
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, reduced_motion="reduce"
        )
        page = context.new_page()
        page.goto(base + "/ui/login")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url("**/ui/overview")
        durations = page.evaluate(
            "Array.from(document.querySelectorAll('.button, button'))"
            ".map(e => getComputedStyle(e).transitionDuration)"
        )
        assert durations and set(durations) == {"0s"}
        assert page.evaluate("document.getAnimations().length") == 0
        context.close()
        browser.close()
