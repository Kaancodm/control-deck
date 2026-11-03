"""Attachment digests describe exactly the bytes inspected by the gate."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from app.core.schemas import TaskV1
from app.privacy import gate
from app.privacy.config import PrivacySettings
from tests.integration.test_database import _minimal_task_payload


def _file_task() -> TaskV1:
    payload = _minimal_task_payload(uuid.uuid4())
    payload["inputs"] = [
        {"type": "text", "name": "brief", "value": "ordinary text"},
        {"type": "file", "name": "attachment", "path": "input.txt"},
    ]
    return TaskV1.model_validate(payload)


def test_digest_preserves_bom_unicode_newlines_and_input_index(tmp_path: Path) -> None:
    data = "\ufeffGrüezi\r\n  ordinary text\n".encode()
    (tmp_path / "input.txt").write_bytes(data)
    report = gate.inspect_task(_file_task(), settings=PrivacySettings(inbox=tmp_path))

    assert len(report.file_hashes) == 1
    assert report.file_hashes[0].input_index == 1
    assert report.file_hashes[0].sha256 == hashlib.sha256(data).hexdigest()
    assert "input.txt" not in repr(report)
    assert "ordinary text" not in repr(report)


def test_digest_changes_when_inspected_bytes_change(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    settings = PrivacySettings(inbox=tmp_path)
    path.write_bytes(b"ordinary text\r\n")
    first = gate.inspect_task(_file_task(), settings=settings)
    path.write_bytes(b"ordinary text\n")
    second = gate.inspect_task(_file_task(), settings=settings)
    assert first.file_hashes != second.file_hashes


def test_each_file_has_its_own_digest_in_input_order(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_bytes(b"first attachment")
    (tmp_path / "second.txt").write_bytes(b"second attachment")
    payload = _file_task().model_dump(mode="json")
    payload["inputs"].append({"type": "file", "name": "second", "path": "second.txt"})
    report = gate.inspect_task(
        TaskV1.model_validate(payload), settings=PrivacySettings(inbox=tmp_path)
    )
    assert [(item.input_index, item.sha256) for item in report.file_hashes] == [
        (1, hashlib.sha256(b"first attachment").hexdigest()),
        (2, hashlib.sha256(b"second attachment").hexdigest()),
    ]


def test_stored_task_treats_missing_file_as_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "get_privacy_settings", lambda: PrivacySettings(inbox=tmp_path))
    with pytest.raises(gate.PrivacyInputUnavailable) as caught:
        gate.inspect_stored_task(_file_task().model_dump(mode="json"))
    assert caught.value.code == "file_unavailable"
    assert "input.txt" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
