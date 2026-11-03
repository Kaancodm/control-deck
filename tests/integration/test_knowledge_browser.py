"""Real browser knowledge selection and explicit export at phone/desktop widths."""

import shutil
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import func, select

from app.core.schemas import DataClass, Worker
from app.kb.state import KnowledgeExport
from app.workers.runner import HostRunner
from app.workers.runner_main import SimulationExecutor
from tests.integration.test_knowledge_store import kb_engine as kb_engine
from tests.integration.test_knowledge_store import knowledge_store as knowledge_store
from tests.integration.test_ui_browser import browser_app as browser_app


@pytest.mark.parametrize("width,javascript", [(390, True), (1024, False)])
def test_knowledge_browser_selection_and_export(
    browser_app, knowledge_store, monkeypatch, tmp_path, width, javascript
):
    monkeypatch.setenv("DECK_KB_PROFILE", "simulation")
    identifier = uuid4()
    knowledge_store.put_document(
        document_id=identifier,
        title="Browser knowledge",
        content="<script>window.contextAttack=true</script>",
        data_class=DataClass.LOCAL_ONLY,
    )
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

        def local_only(route):
            if urlsplit(route.request.url).netloc != urlsplit(base).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", local_only)
        page.goto(base + "/ui/knowledge")
        page.get_by_label("Passwort", exact=True).fill(password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.get_by_role("link", name="Wissen", exact=True).click()
        page.get_by_label("Suchtext", exact=True).fill("Synthetic knowledge")
        page.get_by_label("Datenklasse des Auftrags (Pflicht)").select_option("local_only")
        page.get_by_role("button", name="Erlaubtes Wissen suchen").click()
        page.get_by_role("link", name="Fuer einen neuen Auftrag auswaehlen").click()
        assert page.get_by_label(
            "Ausgewaehlte Wissensreferenzen", exact=False
        ).input_value() == str(identifier)
        page.get_by_label("Titel", exact=True).fill("Knowledge browser task")
        page.get_by_label("Anweisung", exact=True).fill("Produce synthetic text.")
        page.get_by_role("button", name="Auftrag zur Start-Freigabe speichern").click()
        page.get_by_role("heading", name="Ausgewaehlter Wissens-Kontext").wait_for()
        page.get_by_text("Pruefsumme und Referenz", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.evaluate("window.contextAttack === undefined")
        page.get_by_label("Auftrag und Datenklasse geprueft").check()
        page.get_by_role("button", name="Start freigeben", exact=True).click()
        assert (
            HostRunner(factory, runs_root=tmp_path / "runs")
            .run_once(Worker.OLLAMA, SimulationExecutor(Worker.OLLAMA))
            .action
            == "completed"
        )
        page.reload()
        page.get_by_label("Ergebnis geprueft", exact=True).check()
        page.get_by_role("button", name="Ergebnis freigeben", exact=True).click()
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(KnowledgeExport)) == 0
        export_form = page.locator("form[action$='/export']")
        export_form.get_by_label("Datenklasse des Wissenseintrags (Pflicht)").select_option(
            "local_only"
        )
        export_form.get_by_role("checkbox").check()
        export_form.get_by_role("button", name="In Wissen uebernehmen", exact=True).click()
        page.get_by_text("Export abgeschlossen", exact=False).wait_for()
        page.wait_for_load_state("load")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            page.evaluate(
                "Array.from(document.querySelectorAll('*')).filter(e => "
                "e.getBoundingClientRect().right > innerWidth)"
                ".map(e => e.tagName + ':' + e.className)"
            )
        )
        assert not external
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(KnowledgeExport)) == 1
        context.close()
        browser.close()
