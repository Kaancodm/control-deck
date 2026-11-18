"""Rule-file checks: TEAM.md is the one home for roles and merge rights."""

from pathlib import Path

import pytest

from scripts.check_rules import check_rules

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = "Roles, merge, and decision classes: see `TEAM.md`.\n"
OUTSIDE = "role or merge rule outside TEAM.md"


def _base(tmp_path: Path) -> None:
    (tmp_path / "TEAM.md").write_text(
        "# TEAM\n\n- Gueltig seit: (Datum setzt der Owner)\n- Status: ENTWURF\n\n"
        "## Wer ist wer\n\n- Builder: Codex.\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text(
        "## Wartet auf dein GO\n\nNichts offen.\n\n" + REFERENCE, encoding="utf-8"
    )
    for name in ("AGENTS.md", "COORDINATION.md", "REVIEW.md"):
        (tmp_path / name).write_text(REFERENCE, encoding="utf-8")
    (tmp_path / "WORKLOG.md").write_text("# Worklog\n", encoding="utf-8")
    (tmp_path / "LEADERBOARD.md").write_text("# Leaderboard\n", encoding="utf-8")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "claude.md").write_text("# Claude\n\n" + REFERENCE, encoding="utf-8")


def test_repository_rules_are_clean() -> None:
    assert check_rules(ROOT) == []


def test_minimal_tree_is_clean(tmp_path: Path) -> None:
    _base(tmp_path)
    assert check_rules(tmp_path) == []


def test_missing_worklog_and_leaderboard(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "WORKLOG.md").unlink()
    (tmp_path / "LEADERBOARD.md").unlink()
    problems = check_rules(tmp_path)
    assert "missing WORKLOG.md" in problems
    assert "missing LEADERBOARD.md" in problems


def test_go_section_must_be_the_first_line(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "# Notes\n\n## Wartet auf dein GO\n\n" + REFERENCE, encoding="utf-8"
    )
    assert any("Wartet auf dein GO" in problem for problem in check_rules(tmp_path))


def test_missing_team_page_fails(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "TEAM.md").unlink()
    assert "missing TEAM.md" in check_rules(tmp_path)


def test_team_page_needs_the_validity_header(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "TEAM.md").write_text("# TEAM\n\n- Status: ENTWURF\n", encoding="utf-8")
    assert "TEAM.md: header line 'Gueltig seit:' is missing" in check_rules(tmp_path)


def test_validity_line_below_the_header_does_not_count(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "TEAM.md").write_text(
        "# TEAM\n\n## Zweck\n\n- Gueltig seit: 2026-11-18\n", encoding="utf-8"
    )
    assert "TEAM.md: header line 'Gueltig seit:' is missing" in check_rules(tmp_path)


def test_validity_line_needs_a_value(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "TEAM.md").write_text("# TEAM\n\nGueltig seit:\n", encoding="utf-8")
    assert "TEAM.md: header line 'Gueltig seit:' is missing" in check_rules(tmp_path)


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", "COORDINATION.md", "REVIEW.md"])
def test_rule_file_without_reference_fails(tmp_path: Path, name: str) -> None:
    _base(tmp_path)
    head = "## Wartet auf dein GO\n\n" if name == "CLAUDE.md" else ""
    (tmp_path / name).write_text(head + "Regeln: siehe AGENTS.md.\n", encoding="utf-8")
    assert f"{name}: no reference to TEAM.md" in check_rules(tmp_path)


def test_prompt_without_reference_fails(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "prompts" / "claude.md").write_text("Rules: see AGENTS.md.\n", encoding="utf-8")
    assert "prompts/claude.md: no reference to TEAM.md" in check_rules(tmp_path)


@pytest.mark.parametrize(
    "line",
    [
        "- **Claude plans.** The planning model is Fable.",
        "- **Cursor builds.**",
        "- **Grok Bot** coordinates. Grok Bot is the only merger.",
        "- **Codex** is not used.",
        "- **Hermes** is the local agent on the deck server.",
        "- Only **Grok Bot** merges, and only after the owner says yes once.",
        "Claude, Fable, Gemini, and Cursor never merge.",
        "Cursor merges when the check is green.",
        "The Engineering Lead has no merge rights.",
        "Der Owner sagt einmal Ja, danach mergt nur Grok Bot.",
        "Merge nur durch Grok Bot nach einmaligem Ja des Owners.",
        "Claude, Fable, Gemini und Cursor mergen nicht.",
        "Fable plant, Astra baut.",
        "Role: plans with Fable.",
    ],
)
def test_role_or_merge_rule_outside_team_page_fails(tmp_path: Path, line: str) -> None:
    _base(tmp_path)
    (tmp_path / "AGENTS.md").write_text(REFERENCE + line + "\n", encoding="utf-8")
    assert f"AGENTS.md:2: {OUTSIDE}" in check_rules(tmp_path)


@pytest.mark.parametrize(
    "line",
    [
        "Role, merge, and decisions: see `TEAM.md`.",
        "Rollen, Merge und Entscheide: siehe `TEAM.md`.",
        "Do **not** force-push or rewrite pushed history.",
        "The owner replies `N. em` or `N. 2`.",
        "Owner review approves to `done` or rejects to `failed`.",
        "Cloud workers are called through the official CLIs only (`claude -p`, `codex exec`).",
        "| Regeln angleichen | Cursor Cloud Agent | #92 | erledigt (gemergt, mergedAt leer) |",
        "CLAUDE.md is guidance only; hard limits belong in hooks.",
    ],
)
def test_references_and_other_rules_pass(tmp_path: Path, line: str) -> None:
    _base(tmp_path)
    (tmp_path / "AGENTS.md").write_text(REFERENCE + line + "\n", encoding="utf-8")
    assert check_rules(tmp_path) == []


@pytest.mark.parametrize(
    "relative",
    [
        "REVIEW.md",
        "prompts/claude.md",
        ".claude/agents/reviewer.md",
        ".claude/commands/plan-ticket.md",
        ".github/pull_request_template.md",
        "tasks/TEMPLATE.md",
    ],
)
def test_every_rule_file_is_watched(tmp_path: Path, relative: str) -> None:
    _base(tmp_path)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REFERENCE + "Only Grok Bot merges.\n", encoding="utf-8")
    assert f"{relative}:2: {OUTSIDE}" in check_rules(tmp_path)


def test_team_page_itself_may_state_roles_and_merge_rules(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "TEAM.md").write_text(
        "# TEAM\n\nGueltig seit: 2026-11-18\n\n## Rollen\n\n- Fable plant.\n- Atik mergt.\n",
        encoding="utf-8",
    )
    assert check_rules(tmp_path) == []


def test_agents_rule_repeated_in_coordination_fails(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "COORDINATION.md").write_text(REFERENCE + "Kein Force-Push.\n", encoding="utf-8")
    assert "COORDINATION.md: repeats a rule from AGENTS.md" in check_rules(tmp_path)
