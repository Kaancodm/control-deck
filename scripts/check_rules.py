#!/usr/bin/env python3
"""Fail when rule files duplicate AGENTS.md or contradict who may merge."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GO_HEADING = "## Wartet auf dein GO"
WATCHED = ("CLAUDE.md", "COORDINATION.md")
OTHER_MERGER = re.compile(
    r"\b(claude|fable|gemini|cursor|hermes|owner|kaan)\b\s+"
    r"(?:may\s+|can\s+|will\s+|does\s+)?(merge|merges|mergt|mergen)\b"
    r"|\b(merge|merges|mergt|mergen)\b\s+(?:the\s+)?"
    r"(claude|fable|gemini|cursor|hermes|owner|kaan)\b",
    re.IGNORECASE,
)
DUPLICATE_RULE = re.compile(
    r"only grok bot merges|nur grok bot|never merge|mergen nicht|do not merge|"
    r"lead full-stack|pair-programming|/advisor|opus 5\.5|no force-push|kein force-push|"
    r"stays public|bleibt oeffentlich|bleibt öffentlich|claude plans|cursor builds",
    re.IGNORECASE,
)


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

    watched = [root / name for name in WATCHED]
    prompts = root / "prompts"
    if prompts.is_dir():
        watched.extend(sorted(prompts.glob("*.md")))
    for path in watched:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if OTHER_MERGER.search(line):
                problems.append(f"{path.relative_to(root)}:{line_no}: other merger than Grok Bot")
        if path.name in WATCHED and DUPLICATE_RULE.search(text):
            problems.append(f"{path.name}: repeats a role or merge rule from AGENTS.md")
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
