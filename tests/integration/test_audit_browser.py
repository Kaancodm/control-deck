"""Real browser proof for session health, audit paging and warnings at two widths."""

import shutil
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright

from app.core.schemas import Worker
from app.operations.models import OperationEvent
from app.workers.lane_status import LaneHealthState, update_lane_status
from tests.integration.test_ui_browser import browser_app as browser_app


@pytest.mark.parametrize("width,javascript", [(390, True), (1024, False)])
def test_audit_and_health_browser_flow(browser_app, monkeypatch, width, javascript):
    base, password, factory = browser_app
    monkeypatch.setattr("app.api.routes.health.get_session_factory", lambda: factory)
    with factory() as session:
        update_lane_status(
            session,
            lane=Worker.CLAUDE,
            status=LaneHealthState.AUTH_REQUIRED,
            reason="simulation_auth_required",
        )
        for _ in range(3):
            session.add(
                OperationEvent(
                    operation_id=uuid4(),
                    operation_type="backup",
                    phase="completed",
                    detail={"rows": 12, "sha256": "a" * 64, "output": "private-browser-marker"},
                )
            )
        session.commit()
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
        external = []

        def local_only(route):
            if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", local_only)
        page.goto(base + "/ui/audit")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.locator('[data-lane-warning="auth_required"]').wait_for()
        assert "Pausenzustand und Anmeldeverfahren prüfen" in page.content()
        page.get_by_text("Menü", exact=True).click()
        page.get_by_role("link", name="Audit", exact=True).click()
        page.get_by_label("Quelle", exact=True).select_option("operation")
        page.get_by_label("Je Seite", exact=True).fill("2")
        page.get_by_role("button", name="Filtern", exact=True).click()
        page.wait_for_load_state("load")
        assert page.locator("ol.timeline > li").count() == 2
        assert "private-browser-marker" not in page.content()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
            "Array.from(document.querySelectorAll('body *'))"
            ".filter(el => el.getBoundingClientRect().right > innerWidth)"
            ".map(el => [el.tagName, el.id, el.className, el.getBoundingClientRect().right])"
        )
        page.get_by_role("link", name="Weitere →", exact=True).click()
        assert page.locator("ol.timeline > li").count() == 1
        with page.expect_download() as download:
            page.get_by_role("link", name="Sichtbare Seite als CSV", exact=True).click()
        assert download.value.suggested_filename == "audit.csv"
        assert download.value.failure() is None
        page.get_by_text("Menü", exact=True).click()
        page.get_by_role("link", name="Lanes", exact=True).click()
        page.wait_for_load_state("load")
        assert "Versuchszähler seit Migration 0016" in page.content()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Browser's HTTP context uses actual cookie scope even without JavaScript.
        response = context.request.get(base + "/api/v1/health")
        assert response.status == 200 and "lanes" in response.json()
        page.get_by_role("button", name="Abmelden", exact=True).click()
        page.wait_for_url("**/ui/login")
        # The login page creates an unauthenticated CSRF session, never owner access.
        assert context.request.get(base + "/api/v1/health").status == 401
        context.clear_cookies()
        assert context.request.get(base + "/api/v1/health").json() == {"status": "ok"}
        assert not external
        browser.close()
