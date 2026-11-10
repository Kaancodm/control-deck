"""Assistant page order: dialog first, explanations folded, a neutral channel title."""

from __future__ import annotations

import re

from tests.integration.test_ui_flows import login
from tests.integration.test_ui_flows import password_hash as password_hash
from tests.integration.test_ui_flows import ui_client as ui_client


def test_assistant_page_puts_the_dialog_first_and_names_no_person(ui_client):
    login(ui_client)
    html = ui_client.get("/ui/assistant").text
    main = html.split("<main", 1)[1]
    fold = main.index("So funktioniert der Assistent")
    assert main.index('id="message"') < fold and main.index("Dialog senden") < fold
    # The three context boxes fold away under "Mehr Optionen" inside the dialog form.
    form = main.split('id="assistant-talk-form"', 1)[1].split("</form>", 1)[0]
    options = form.split("<summary>Mehr Optionen</summary>", 1)[1]
    for name in ("include_profile", "include_history", "persist"):
        assert f'name="{name}"' in options
    assert "<h2>Kanal</h2>" in main and "<strong>Nicht angebunden.</strong>" in main
    assert ">missing<" not in main and "Kanal missing" not in main
    # Exactly one filled submit button; every other one is quiet.
    assert re.findall(r"<button>([^<]+)</button>", main) == ["Dialog senden"]
    assert '<p class="assistant-lede">Standard ist der Mock, kein Live-Modell.</p>' in main
    assert "Router-Golden-Set" in main and "Denkphase" in main
