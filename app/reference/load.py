"""Load reference cases from YAML files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.reference.validate import LoadedReferenceCase, load_reference_case_dict


@dataclass(frozen=True)
class CaseLoadIssue:
    path: Path
    message: str


def load_case_file(path: Path) -> LoadedReferenceCase:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected mapping at root")
    loaded = load_reference_case_dict(raw)
    if loaded.case.id != path.stem:
        raise ValueError(f"{path}: id {loaded.case.id!r} must match filename stem {path.stem!r}")
    return loaded


def load_all_cases(cases_dir: Path) -> tuple[list[LoadedReferenceCase], list[CaseLoadIssue]]:
    paths = sorted(cases_dir.glob("*.yaml"))
    cases: list[LoadedReferenceCase] = []
    issues: list[CaseLoadIssue] = []
    seen_ids: set[str] = set()

    for path in paths:
        try:
            loaded = load_case_file(path)
        except Exception as exc:
            issues.append(CaseLoadIssue(path=path, message=str(exc)))
            continue
        if loaded.case.id in seen_ids:
            issues.append(CaseLoadIssue(path=path, message=f"duplicate case id {loaded.case.id!r}"))
            continue
        seen_ids.add(loaded.case.id)
        cases.append(loaded)

    return cases, issues
