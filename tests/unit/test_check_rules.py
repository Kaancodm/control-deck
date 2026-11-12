"""Rule-file checks: one home for roles and merge rights, plus the required lists."""

from pathlib import Path

from scripts.check_rules import check_rules

ROOT = Path(__file__).resolve().parents[2]


def _base(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "## Wartet auf dein GO\n\nNichts offen.\n", encoding="utf-8"
    )
    (tmp_path / "COORDINATION.md").write_text("Regeln: siehe AGENTS.md.\n", encoding="utf-8")
    (tmp_path / "WORKLOG.md").write_text("# Worklog\n", encoding="utf-8")
    (tmp_path / "LEADERBOARD.md").write_text("# Leaderboard\n", encoding="utf-8")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "claude.md").write_text("Role: plans.\nRules: see AGENTS.md.\n", encoding="utf-8")


def test_repository_rules_are_clean() -> None:
    assert check_rules(ROOT) == []


def test_missing_worklog_and_leaderboard(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "WORKLOG.md").unlink()
    (tmp_path / "LEADERBOARD.md").unlink()
    problems = check_rules(tmp_path)
    assert "missing WORKLOG.md" in problems
    assert "missing LEADERBOARD.md" in problems


def test_go_section_must_be_the_first_line(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Notes\n\n## Wartet auf dein GO\n", encoding="utf-8")
    assert any("Wartet auf dein GO" in problem for problem in check_rules(tmp_path))


def test_other_merger_fails(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "prompts" / "claude.md").write_text(
        "Cursor merges when the check is green.\n",
        encoding="utf-8",
    )
    problems = check_rules(tmp_path)
    assert any("other merger than Grok Bot" in problem for problem in problems)


def test_duplicate_role_rule_in_coordination_fails(tmp_path: Path) -> None:
    _base(tmp_path)
    (tmp_path / "COORDINATION.md").write_text(
        "Claude plans and Cursor builds.\n",
        encoding="utf-8",
    )
    problems = check_rules(tmp_path)
    assert any("repeats a role or merge rule" in problem for problem in problems)
