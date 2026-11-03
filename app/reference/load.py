"""Load reference cases from YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.reference.validate import LoadedReferenceCase, load_reference_case_dict


def load_case_file(path: Path) -> LoadedReferenceCase:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected mapping at root")
    return load_reference_case_dict(raw)


def load_all_cases(cases_dir: Path) -> list[LoadedReferenceCase]:
    paths = sorted(cases_dir.glob("*.yaml"))
    return [load_case_file(path) for path in paths]
