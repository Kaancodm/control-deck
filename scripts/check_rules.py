#!/usr/bin/env python3
"""Fail when role or merge rules live outside TEAM.md or a rule file skips the reference."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GO_HEADING = "## Wartet auf dein GO"
TEAM_PAGE = "TEAM.md"
# The header ends at the first section heading; it must carry the validity line.
VALID_SINCE = re.compile(r"^(?:[-*]\s+)?Gueltig seit:\s*\S")
# Rule files besides prompts/*.md. Each one points to TEAM.md and restates nothing from it.
RULE_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "COORDINATION.md",
    "REVIEW.md",
    ".claude/agents/reviewer.md",
    ".claude/commands/plan-ticket.md",
    ".github/pull_request_template.md",
    "tasks/TEMPLATE.md",
)
# These two also follow AGENTS.md and must not restate its rules.
AGENTS_FOLLOWERS = ("CLAUDE.md", "COORDINATION.md")
_ACTOR = (
    r"(?:grok bot|supergrok|atik|astra|codex|claude code|claude|fable|opus|gemini pro|gemini|"
    r"cursor|hermes|kaan|engineering lead|owner|sammler|postfach|planer|planner|builder|"
    r"reviewer|hilfsbots?)"
)
_MERGE = r"(?:merge|merges|mergt|mergen|merget)"
# An actor as the subject of a sentence; Markdown emphasis may close right after the name.
_SUBJECT = rf"\b{_ACTOR}[*_`]*\s+"
ROLE_OR_MERGE_RULE = re.compile(
    rf"{_SUBJECT}(?:(?:may|can|will|does|must|never|only|darf|nie|nur)\s+)*{_MERGE}\b"
    rf"|\b{_MERGE}\s+(?:(?:the|only|nur|durch|by)\s+)*[*_`]*{_ACTOR}\b"
    r"|\bnever merges?\b|\bdo(?:es)? not merge\b|\b(?:mergen|mergt|merget) nicht\b"
    r"|\bonly merger\b|\bmerge rights?\b|\bmerge-recht"
    rf"|{_SUBJECT}(?:plans|builds|reviews|coordinates|researches)\b"
    rf"|{_SUBJECT}(?:plant|baut|prueft|prüft|reviewt|koordiniert|recherchiert)\b"
    rf"|{_SUBJECT}(?:is|ist)\s+(?:the|not|lead|der|die|das|kein|keine|nicht)\b"
    r"|^\W*(?:role|rolle)\s*:\s*(?!see\b|siehe\b)",
    re.IGNORECASE,
)
AGENTS_DUPLICATE = re.compile(
    r"lead full-stack|pair-programming|/advisor|opus 5\.5|no force-push|kein force-push|"
    r"stays public|bleibt oeffentlich|bleibt öffentlich",
    re.IGNORECASE,
)


def _team_page_problems(root: Path) -> list[str]:
    team = root / TEAM_PAGE
    if not team.is_file():
        return [f"missing {TEAM_PAGE}"]
    for line in team.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            break
        if VALID_SINCE.match(line):
            return []
    return [f"{TEAM_PAGE}: header line 'Gueltig seit:' is missing"]


def _rule_files(root: Path) -> list[Path]:
    paths = [root / name for name in RULE_FILES]
    prompts = root / "prompts"
    if prompts.is_dir():
        paths.extend(sorted(prompts.glob("*.md")))
    return [path for path in paths if path.is_file()]


def check_rules(root: Path) -> list[str]:
    """Return human-readable problems. An empty list means the tree is clean."""
    problems: list[str] = []
    for name in ("WORKLOG.md", "LEADERBOARD.md"):
        if not (root / name).is_file():
            problems.append(f"missing {name}")

    claude = root / "CLAUDE.md"
    if not claude.is_file():
        problems.append("missing CLAUDE.md")
    else:
        lines = claude.read_text(encoding="utf-8").splitlines()
        first = next((line.strip() for line in lines if line.strip()), "")
        if first != GO_HEADING:
            problems.append("CLAUDE.md must start with 'Wartet auf dein GO'")

    problems.extend(_team_page_problems(root))

    for path in _rule_files(root):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if TEAM_PAGE not in text:
            problems.append(f"{relative}: no reference to {TEAM_PAGE}")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if ROLE_OR_MERGE_RULE.search(line):
                problems.append(f"{relative}:{line_no}: role or merge rule outside {TEAM_PAGE}")
        if relative in AGENTS_FOLLOWERS and AGENTS_DUPLICATE.search(text):
            problems.append(f"{relative}: repeats a rule from AGENTS.md")
    return problems


def main() -> int:
    problems = check_rules(ROOT)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
