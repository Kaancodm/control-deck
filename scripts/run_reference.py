#!/usr/bin/env python3
"""CLI to run reference test cases against explicitly simulated executors."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.reference.load import load_all_cases
from app.reference.ollama import OllamaReferenceExecutor
from app.reference.runner import Executor, FakeExecutor, format_summary, run_cases

_DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "reference" / "cases"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Control Deck reference cases")
    parser.add_argument(
        "--executor",
        choices=("fake", "ollama-fake-cli"),
        default="fake",
        help="Use the deterministic fake or the bundled Ollama CLI simulation",
    )
    parser.add_argument("--model", help="Explicit fixture model for ollama-fake-cli")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=_DEFAULT_CASES,
        help="Directory containing NNN-slug.yaml reference cases",
    )
    args = parser.parse_args(argv)
    executor: Executor
    if args.executor == "ollama-fake-cli":
        if not args.model:
            parser.error("--executor ollama-fake-cli requires --model")
        try:
            executor = OllamaReferenceExecutor(model=args.model)
        except ValueError:
            parser.error("Invalid Ollama simulation configuration")
    else:
        if args.model is not None:
            parser.error("--model requires --executor ollama-fake-cli")
        executor = FakeExecutor()

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

    summary = run_cases(cases, executor)
    print(format_summary(summary))
    failed = summary.failed + len(load_issues)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
