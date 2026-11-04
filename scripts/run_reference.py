#!/usr/bin/env python3
"""CLI to run reference cases and distinguish live model evidence from simulation."""

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
        choices=("fake", "ollama-fake-cli", "ollama-live"),
        default="fake",
        help="Select simulation or flag-gated live Ollama",
    )
    parser.add_argument("--model", help="Explicit fixture or configured live model")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=_DEFAULT_CASES,
        help="Directory containing NNN-slug.yaml reference cases",
    )
    args = parser.parse_args(argv)
    executor: Executor
    if args.executor in {"ollama-fake-cli", "ollama-live"}:
        if not args.model:
            parser.error("The Ollama executor requires --model")
        try:
            executor = OllamaReferenceExecutor(
                model=args.model,
                execution_profile="live" if args.executor == "ollama-live" else "simulation",
            )
        except (ValueError, RuntimeError):
            parser.error("Invalid Ollama reference configuration")
    else:
        if args.model is not None:
            parser.error("--model requires an Ollama executor")
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
