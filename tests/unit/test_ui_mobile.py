"""Phone-friendly owner UI: compact navigation, real umlauts, and a short task form."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.core.schemas import DataClass
from app.ui import views
from app.ui.files import UiFileError
from app.ui.forms import FormError

TEMPLATES = Path(views.__file__).with_name("templates")
TRANSLITERATED = re.compile(
    r"\b\w*(Auftraeg|Ueberall|pruef|Pruef|fuer|Fuer|verfueg|bestaetig|waehl|benoetig|"
    r"Laeuft|zurueck|Zurueck|Gruend|Begruend|unveraender|Naechst|Eintraeg|uebernehm|"
    r"unabhaeng|Parallelitaet|zaehler|Ausfuehr|gemaess|ausdruecklich|ausloes|gehoer|"
    r"vollstaendig|Zusaetz|Einschraenk|Unzulaess|geaender)\w*\b"
)


class Outline(HTMLParser):
    """Record form fields, nav links and logout buttons with their enclosing <details>."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.details: list[str] = []
        self.summary_text = ""
        self.in_summary = False
        self.fields: dict[str, tuple[str, ...]] = {}
        self.links: list[tuple[str, tuple[str, ...]]] = []
        self.buttons: list[tuple[str, tuple[str, ...]]] = []
        self.in_button = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "details":
            self.details.append("")
        elif tag == "summary":
            self.in_summary = True
            self.summary_text = ""
        elif tag in ("input", "select", "textarea") and values.get("name"):
            if values.get("type") != "hidden":
                self.fields[values["name"]] = tuple(self.details)
        elif tag == "a" and values.get("href", "").startswith("/ui/"):
            self.links.append((values["href"], tuple(self.details)))
        elif tag == "button":
            self.in_button = True
            self.button_text = ""

    def handle_data(self, data: str) -> None:
        if self.in_summary:
            self.summary_text += data
        if self.in_button:
            self.button_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "summary":
            self.in_summary = False
            self.details[-1] = self.summary_text.strip()
        elif tag == "details":
            self.details.pop()
        elif tag == "button":
            self.in_button = False
            self.buttons.append((self.button_text.strip(), tuple(self.details)))


def render(name: str, **context: object) -> str:
    base = {"owner": True, "csrf": "synthetic-csrf", "controls": None, "lane_health": []}
    return views.TEMPLATES.env.get_template(name).render({**base, **context})


class Regions(HTMLParser):
    """Collect link targets per shell region: sidebar, menu navigation and tab bar."""

    REGIONS = ("sidebar", "menu-nav", "tabbar")

    def __init__(self, html: str) -> None:
        super().__init__()
        self.stack: list[str | None] = []
        self.links: dict[str, list[str]] = {name: [] for name in self.REGIONS}
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        region = next((name for name in self.REGIONS if name in classes), None)
        if tag == "a":
            current = next((name for name in reversed(self.stack) if name), None)
            if current:
                self.links[current].append(dict(attrs).get("href") or "")
        elif tag not in {"svg", "path", "rect", "circle", "input", "br", "meta", "link"}:
            self.stack.append(region)

    def handle_endtag(self, tag: str) -> None:
        if tag not in {"a", "svg", "path", "rect", "circle", "input", "br", "meta", "link"}:
            self.stack.pop()


def test_shell_navigation_matrix_one_logout_and_a_menu() -> None:
    # Replaces the former three-link phone header (K10): sidebar, menu and tab bar.
    html = render("error.html", error=None)
    regions = Regions(html)
    assert regions.links["sidebar"][0] == "/ui/overview"  # brand
    assert regions.links["sidebar"][1:] == [
        "/ui/overview",
        "/ui/tasks",
        "/ui/tasks/new",
        "/ui/knowledge",
        "/ui/lanes",
        "/ui/assistant",
        "/ui/overview#meldungen",
        "/ui/overview#offen",
        "/ui/chat",
        "/ui/audit",
    ]
    assert regions.links["tabbar"] == [
        "/ui/overview",
        "/ui/lanes",
        "/ui/assistant",
        "/ui/overview#meldungen",
        "/ui/overview#offen",
    ]
    assert regions.links["menu-nav"] == [
        "/ui/tasks",
        "/ui/tasks/new",
        "/ui/knowledge",
        "/ui/chat",
        "/ui/audit",
    ]
    assert not set(regions.links["menu-nav"]) & set(regions.links["tabbar"])
    assert set(regions.links["menu-nav"]) | set(regions.links["tabbar"]) == set(
        regions.links["sidebar"][1:]
    )
    outline = Outline(html)
    assert [text for text, nesting in outline.buttons if not nesting] == ["Abmelden"]
    assert [text for text, nesting in outline.buttons if nesting == ("Menü",)] == [
        "Überall abmelden"
    ]
    assert html.count(">Menü<") == 1
    tab_labels = re.findall(r'class="tabbar".*?</nav>', html, re.S)[0]
    assert [label for label in re.findall(r"<span>([^<]+)</span>", tab_labels)] == [
        "Übersicht",
        "Lanes",
        "Assistent",
        "Meldungen",
        "Offen",
    ]


@pytest.mark.parametrize(
    "path,key",
    [
        ("/ui/overview", "overview"),
        ("/ui/tasks", "tasks"),
        ("/ui/tasks/0b6f6a8e-3c1d-4d1e-9a51-2f7a7e1d1c11", "tasks"),
        ("/ui/tasks/new", "new"),
        ("/ui/failed", "tasks"),
        ("/ui/knowledge", "knowledge"),
        ("/ui/knowledge/search", "knowledge"),
        ("/ui/lanes", "lanes"),
        ("/ui/assistant", "assistant"),
        ("/ui/assistant/orders/x", "assistant"),
        ("/ui/chat", "chat"),
        ("/ui/audit", "audit"),
        ("/ui/audit.csv", ""),
        ("/ui/login", ""),
        ("/", ""),
        ("/ui/tasksx", ""),
    ],
)
def test_nav_key_uses_the_longest_prefix(path: str, key: str) -> None:
    assert views.nav_key(path) == key


def test_active_navigation_is_marked_in_every_region() -> None:
    html = render("error.html", error=None, nav="lanes")
    assert html.count('href="/ui/lanes" aria-current="page"') == 2  # sidebar and tab bar
    assert 'href="/ui/tasks" aria-current="page"' not in html


def test_guest_pages_have_no_navigation() -> None:
    html = render("login.html", owner=False)
    for marker in ('class="sidebar"', 'class="tabbar"', "hx-get", ">Menü<", "Abmelden"):
        assert marker not in html


def test_task_form_shows_only_title_and_instructions_until_more_options_is_opened() -> None:
    html = render("task_form.html", values={})
    outline = Outline(html)
    assert outline.fields["title"] == ()
    assert outline.fields["instructions"] == ()
    folded = {name for name, nesting in outline.fields.items() if nesting[:1] == ("Mehr Optionen",)}
    assert folded == set(outline.fields) - {"title", "instructions", "csrf"}
    assert "<script" not in html.split("</head>")[1]
    assert '<details class="more">' in html
    assert "Datenklasse: Nur lokal" in html


@pytest.mark.parametrize(
    "values",
    [{"data_class": "cloud_ok"}, {"input_text": "x"}, {"context_refs": "ref"}],
)
def test_prefilled_options_open_the_folded_section(values: dict[str, str]) -> None:
    assert '<details class="more" open>' in render("task_form.html", values=values)


def test_data_classes_are_shown_with_german_names_but_submitted_by_identifier() -> None:
    html = render("task_form.html", values={})
    for value in DataClass:
        label = views.DATA_CLASS_LABELS[value.value]
        assert f'<option value="{value.value}" selected>{label}</option>' in html or (
            f'<option value="{value.value}" >{label}</option>' in html
        )
    assert set(views.DATA_CLASS_LABELS) == {value.value for value in DataClass}


def test_every_ui_error_code_has_a_concrete_message() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(views.__file__).parent.glob("*.py")
        if path.name != "views.py"
    )
    codes = set(re.findall(r'(?:UiError|FormError|UiFileError)\("([a-z_]+)"', source))
    codes |= set(re.findall(r'error="([a-z_]+)"', source))
    codes |= set(re.findall(r'"error": "([a-z_]+)"', source))
    missing = sorted(codes - set(views.ERROR_MESSAGES))
    assert missing == [], missing
    for problem, action in views.ERROR_MESSAGES.values():
        assert problem.endswith(".") and action.endswith(".")
        assert TRANSLITERATED.search(problem) is None and TRANSLITERATED.search(action) is None


def test_error_banner_names_the_problem_and_the_next_step() -> None:
    html = render("task_form.html", values={}, error="cloud_confirmation_required")
    assert "Für eine Cloud-Datenklasse fehlt die Bestätigung." in html
    assert "Cloud-Versand ausdrücklich bestätigen" in html
    assert "(Code: cloud_confirmation_required)" in html
    unknown = render("error.html", error="synthetic_unknown_code")
    assert "Diese Aktion ist nicht möglich." in unknown and "synthetic_unknown_code" in unknown


def test_form_error_codes_are_fixed_strings_safe_to_render() -> None:
    assert FormError().code == "invalid_task"
    assert UiFileError("file_too_large").code == "file_too_large"


def test_templates_use_real_umlauts() -> None:
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = re.sub(r"<[^>]+>|\{\{.*?\}\}|\{%.*?%\}", " ", path.read_text(encoding="utf-8"))
        assert TRANSLITERATED.search(text) is None, (path.name, TRANSLITERATED.search(text))
    assert views.STATUS_LABELS["running"][1] == "Läuft"
