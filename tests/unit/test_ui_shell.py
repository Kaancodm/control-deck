"""Static shell invariants: no inline code, no external assets, zoomable viewport, contrast."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ui import views

UI = Path(views.__file__).parent
TEMPLATES = UI / "templates"
STATIC = UI / "static"
OWN_ASSETS = sorted(
    path
    for path in STATIC.rglob("*")
    if path.suffix in {".css", ".js"} and "vendor" not in path.relative_to(STATIC).parts
)
TAG = re.compile(r"<[a-zA-Z][^>]*>", re.S)


def template_sources() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(TEMPLATES)), path.read_text(encoding="utf-8"))
        for path in sorted(TEMPLATES.rglob("*.html"))
    ]


@pytest.mark.parametrize("name,source", template_sources())
def test_templates_have_no_inline_style_or_script(name: str, source: str) -> None:
    assert "<style" not in source.lower(), name
    assert "javascript:" not in source.lower(), name
    for tag in TAG.findall(source):
        assert not re.search(r"\sstyle\s*=", tag), (name, tag)
        assert not re.search(r"\son[a-z]+\s*=", tag), (name, tag)
        if tag.startswith("<script"):
            match = re.search(r'\ssrc="([^"]+)"', tag)
            assert match is not None, (name, tag)
            assert match.group(1).startswith("/ui/static/"), (name, tag)
        if tag.startswith("<link"):
            match = re.search(r'\shref="([^"]+)"', tag)
            assert match is not None and match.group(1).startswith("/ui/static/"), (name, tag)
        assert not re.search(r'\s(?:src|href)="(?:https?:)?//', tag), (name, tag)


def test_own_assets_reference_nothing_external() -> None:
    assert {path.name for path in OWN_ASSETS} >= {"app.css", "overview.css", "assistant.css"}
    for path in OWN_ASSETS:
        text = path.read_text(encoding="utf-8")
        for marker in ("http://", "https://", "//", "@import", "@font-face", "fonts.g", "url("):
            assert marker not in text, (path.name, marker)


def test_viewport_never_locks_zoom() -> None:
    source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    (viewport,) = re.findall(r'<meta name="viewport" content="([^"]+)">', source)
    assert "width=device-width" in viewport and "initial-scale=1" in viewport
    for forbidden in ("maximum-scale", "minimum-scale", "user-scalable"):
        assert forbidden not in viewport
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    # Inputs stay at 16 px so iOS Safari does not zoom on focus.
    assert "font-size: 16px" in css
    assert "outline: none" not in css and "outline:none" not in css
    assert "prefers-reduced-motion: reduce" in css


def test_shell_keeps_one_main_no_h1_and_one_menu() -> None:
    html = views.TEMPLATES.env.get_template("error.html").render(
        owner=True, csrf="synthetic", controls=None, lane_health=[], error=None
    )
    assert html.count("<main") == 1
    assert html.count("<h1") == 1  # the error page's own heading only
    assert html.count(">Menü<") == 1
    assert 'id="control-status"' not in html  # only rendered with controls
    assert "data-lane-warning" not in html


def _tokens() -> dict[str, str]:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", root))


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("text", ["text", "text-2", "muted", "accent"])
@pytest.mark.parametrize("surface", ["panel", "side", "bg", "raised"])
def test_text_tokens_meet_aa_on_surfaces(text: str, surface: str) -> None:
    tokens = _tokens()
    assert contrast(tokens[f"color-{text}"], tokens[f"color-{surface}"]) >= 4.5


@pytest.mark.parametrize(
    "foreground,background",
    [
        ("action-text", "action"),
        ("action-text", "action-top"),
        ("on-danger", "danger"),
        ("on-warning", "warning"),
    ],
)
def test_text_on_filled_controls_meets_aa(foreground: str, background: str) -> None:
    tokens = _tokens()
    assert contrast(tokens[f"color-{foreground}"], tokens[f"color-{background}"]) >= 4.5


@pytest.mark.parametrize("surface", ["panel", "bg"])
def test_field_borders_meet_non_text_contrast(surface: str) -> None:
    tokens = _tokens()
    assert contrast(tokens["color-control-border"], tokens[f"color-{surface}"]) >= 3.0


def test_fixed_colours_live_only_in_the_tokens() -> None:
    for path in OWN_ASSETS:
        if path.suffix != ".css":
            continue
        css = path.read_text(encoding="utf-8")
        if path.name == "app.css":
            css = css.split(":root {", 1)[0] + css.split(":root {", 1)[1].split("}", 1)[1]
        assert re.findall(r"#[0-9A-Fa-f]{3,8}\b", css) == [], path.name


def test_phone_header_and_tab_bar_are_opaque_and_stacked() -> None:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    phone = css.split("@media (max-width: 53.75rem) {", 1)[1].split("\n}\n", 1)[0]
    assert "position: relative; z-index: 7;" in phone
    assert "backdrop-filter: none" in phone
    assert "z-index: 6" in phone  # the tab bar stays below the open menu
    assert ".tabbar { display: none; }" in phone and "body:has(" in phone
    assert not re.search(r"\.tabbar[^{}]*\{[^}]*blur\(", css)  # nothing shines through
    tabbar = phone.split(".tabbar {", 1)[1].split("}", 1)[0]
    assert "background: var(--color-side);" in tabbar
    assert not re.search(r"[\s;{]order\s*:", css)  # DOM order equals visual order in the header
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.index(">Abmelden</button>") < base.index("<summary>Menü</summary>")


def test_theme_token_names_are_stable() -> None:
    tokens = _tokens()
    for name in ("bg", "panel", "raised", "text", "muted", "accent", "action", "action-text"):
        assert f"color-{name}" in tokens
    for name in ("danger", "success", "warning"):
        assert f"color-{name}" in tokens
