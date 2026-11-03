#!/usr/bin/env python3
"""CLI to run reference test cases against the fake executor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.reference.load import load_all_cases
from app.reference.runner import FakeExecutor, format_summary, run_cases

_DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "reference" / "cases"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Control Deck reference cases")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=_DEFAULT_CASES,
        help="Directory containing NNN-slug.yaml reference cases",
    )
    args = parser.parse_args(argv)

    cases_dir: Path = args.cases_dir
    if not cases_dir.is_dir():
        print(f"cases directory not found: {cases_dir}", file=sys.stderr)
        return 1

    cases, load_issues = load_all_cases(cases_dir)
    for issue in load_issues:
        print(f"LOAD FAIL {issue.path.name}: {issue.message}", file=sys.stderr)

    if not cases:
        print(f"no runnable cases found in {cases_dir}", file=sys.stderr)
        return 1

    summary = run_cases(cases, FakeExecutor())
    print(format_summary(summary))
    failed = summary.failed + len(load_issues)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
