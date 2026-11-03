"""Privacy policy, metadata coverage, limits, and local configuration contracts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.schemas import TaskV1
from app.privacy.config import PrivacyConfigurationError, PrivacySettings, get_privacy_settings
from app.privacy.gate import PrivacyInputError, inspect_task, validate_submission
from tests.integration.test_database import _minimal_task_payload


def _task(**changes: object) -> TaskV1:
    payload = _minimal_task_payload(uuid.uuid4())
    payload.update(changes)
    return TaskV1.model_validate(payload)


@pytest.fixture
def settings(tmp_path: Path) -> PrivacySettings:
    return PrivacySettings(inbox=tmp_path, blocklist_configured=True)


@pytest.mark.parametrize("data_class", ["local_only", "cloud_redacted", "cloud_ok"])
def test_secrets_are_rejected_for_every_class(
    settings: PrivacySettings, monkeypatch: pytest.MonkeyPatch, data_class: str
) -> None:
    from app.privacy import gate

    monkeypatch.setattr(gate, "get_privacy_settings", lambda: settings)
    secret = "sk-" + "TEST-PRIVATE-MARKER"
    task = _task(data_class=data_class, instructions=secret)
    with pytest.raises(PrivacyInputError) as caught:
        validate_submission(task)
    assert caught.value.code == "secret_detected"
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "data_class,blocked", [("local_only", False), ("cloud_ok", True), ("cloud_redacted", True)]
)
def test_personal_data_policy(settings: PrivacySettings, data_class: str, blocked: bool) -> None:
    email = "sample" + "@" + "example.invalid"
    report = inspect_task(_task(data_class=data_class, instructions=email), settings=settings)
    assert report.blocked is blocked
    assert report.has_secrets is False
    assert email not in repr(report)
    assert email not in json.dumps(report.safe_detail())


@pytest.mark.parametrize("data_class", ["cloud_ok", "cloud_redacted"])
def test_cloud_requires_explicit_blocklist_configuration(tmp_path: Path, data_class: str) -> None:
    with pytest.raises(PrivacyConfigurationError):
        inspect_task(_task(data_class=data_class), settings=PrivacySettings(inbox=tmp_path))


def test_local_task_does_not_require_blocklist(tmp_path: Path) -> None:
    assert inspect_task(_task(), settings=PrivacySettings(inbox=tmp_path)).blocked is False


def test_secret_in_late_field_survives_finding_cap(settings: PrivacySettings) -> None:
    email = "sample" + "@" + "example.invalid "
    secret = "sk-" + "TEST-LATE-MARKER"
    task = _task(instructions=email * 200, labels={"note": secret})
    report = inspect_task(task, settings=settings)
    assert report.has_secrets is True
    assert report.blocked is True
    assert len(report.findings) <= 128
    assert secret not in json.dumps(report.safe_detail())


@pytest.mark.parametrize(
    "changes",
    [
        {"labels": {"password": "synthetic-value"}},
        {"inputs": [{"type": "text", "name": "api_key", "value": "synthetic-value"}]},
    ],
)
def test_credential_names_are_scanned_with_their_values(
    settings: PrivacySettings, changes: dict[str, object]
) -> None:
    report = inspect_task(_task(**changes), settings=settings)
    assert report.has_secrets is True
    assert "synthetic-value" not in json.dumps(report.safe_detail())


@pytest.mark.parametrize(
    "changes",
    [
        {"instructions": "x" * (5 * 1024 * 1024 + 1)},
        {"inputs": [{"type": "text", "name": "input", "value": "ok"}] * 65},
        {"inputs": [{"type": "file", "name": "input", "path": "input.txt"}] * 17},
        {"labels": {str(index): "" for index in range(600)}},
    ],
)
def test_oversized_tasks_are_rejected_without_truncation(
    settings: PrivacySettings, changes: dict[str, object]
) -> None:
    with pytest.raises(PrivacyInputError):
        inspect_task(_task(**changes), settings=settings)


def test_unresolved_context_is_not_silently_ignored(settings: PrivacySettings) -> None:
    with pytest.raises(PrivacyInputError) as caught:
        inspect_task(
            _task(context_refs=[{"source": "knowledge_db", "ref": "fixture-reference"}]),
            settings=settings,
        )
    assert caught.value.code == "context_unavailable"


def test_blocklist_reloads_and_preserves_literals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "local-literals.txt"
    path.write_text("# Local configuration\nProject Fixture\nProject Fixture\n", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    first = get_privacy_settings()
    assert first.blocklist == ("Project Fixture",)
    assert "Project Fixture" not in repr(first)
    assert str(path) not in repr(first)
    path.write_text("", encoding="utf-8")
    second = get_privacy_settings()
    assert second.blocklist == ()
    assert second.blocklist_configured is True


@pytest.mark.parametrize(
    "mode", ["missing", "symlink", "directory", "invalid_utf8", "oversized", "control"]
)
def test_bad_blocklist_configuration_fails_without_echoing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    path = tmp_path / "private-config-marker"
    if mode == "symlink":
        target = tmp_path / "target"
        target.write_text("", encoding="utf-8")
        path.symlink_to(target)
    elif mode == "directory":
        path.mkdir()
    elif mode == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif mode == "oversized":
        path.write_bytes(b"x" * 65537)
    elif mode == "control":
        path.write_text("private\x00literal", encoding="utf-8")
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    with pytest.raises(PrivacyConfigurationError) as caught:
        get_privacy_settings()
    assert str(path) not in str(caught.value)
    assert "private" not in str(caught.value)


def test_blocklist_cannot_live_in_project_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.privacy import config

    path = tmp_path / "blocklist.txt"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DECK_BLOCKLIST_PATH", str(path))
    with pytest.raises(PrivacyConfigurationError) as caught:
        get_privacy_settings()
    assert caught.value.code == "blocklist_must_be_external"
