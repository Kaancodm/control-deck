"""Checks for the ADR 0010 auto-merge guard workflow."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-merge-guard.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _trigger(workflow: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def test_guard_runs_on_every_push_to_a_pull_request() -> None:
    assert _trigger(_workflow()) == {"pull_request": {"types": ["synchronize"]}}


def test_guard_does_not_check_out_or_run_pull_request_code() -> None:
    workflow = _workflow()
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    assert all("uses" not in step for step in steps)
    assert workflow["permissions"] == {"contents": "read"}


def test_guard_disables_auto_merge() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh pr merge" in text and "--disable-auto" in text
    assert "--auto " not in text.replace("--disable-auto", "")
