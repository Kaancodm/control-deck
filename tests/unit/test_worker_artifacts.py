"""Worker result artifacts remain private, immutable, and within their attempt."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.redaction.artifacts import write_payload_sent
from app.workers.artifacts import (
    WorkerArtifactError,
    initialize_attempt_output,
    write_worker_artifact,
)


def _prepared(tmp_path: Path) -> tuple[Path, object, Path]:
    task_id = uuid4()
    root = tmp_path / "runs"
    write_payload_sent(root, task_id=task_id, attempt=1, text="prepared bytes")
    return root, task_id, root / str(task_id) / "1"


def test_result_is_private_and_exact_bytes_and_repeated_write_is_idempotent(tmp_path: Path) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    data = '{"text":"Ä\\n"}\n'.encode()
    one = write_worker_artifact(root, task_id=task_id, attempt=1, name="result.json", data=data)
    two = write_worker_artifact(root, task_id=task_id, attempt=1, name="result.json", data=data)
    assert one == two
    assert (attempt / "result.json").read_bytes() == data
    assert (attempt / "result.json").stat().st_mode & 0o777 == 0o600
    assert (attempt / "result.json").stat().st_nlink == 1
    assert one.size_bytes == len(data)


def test_output_directory_is_private_and_cannot_escape(tmp_path: Path) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    assert initialize_attempt_output(root, task_id=task_id, attempt=1) == attempt
    assert (attempt / "out").stat().st_mode & 0o777 == 0o700
    initialize_attempt_output(root, task_id=task_id, attempt=1)


def test_conflicting_result_is_never_overwritten(tmp_path: Path) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    write_worker_artifact(root, task_id=task_id, attempt=1, name="result.json", data=b"first")
    with pytest.raises(WorkerArtifactError, match="stored safely"):
        write_worker_artifact(root, task_id=task_id, attempt=1, name="result.json", data=b"second")
    assert (attempt / "result.json").read_bytes() == b"first"


@pytest.mark.parametrize(
    "name", ["../result.json", "out/result.json", "payload.sent.md", "other.log"]
)
def test_only_bounded_result_and_lane_log_names_are_accepted(tmp_path: Path, name: str) -> None:
    root, task_id, _ = _prepared(tmp_path)
    with pytest.raises(WorkerArtifactError):
        write_worker_artifact(root, task_id=task_id, attempt=1, name=name, data=b"safe")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public"])
def test_unsafe_existing_result_is_rejected(tmp_path: Path, kind: str) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    target = tmp_path / "outside"
    target.write_bytes(b"safe")
    target.chmod(0o600)
    path = attempt / "result.json"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"safe")
        path.chmod(0o644)
    with pytest.raises(WorkerArtifactError):
        write_worker_artifact(root, task_id=task_id, attempt=1, name="result.json", data=b"safe")
    assert target.read_bytes() == b"safe"


def test_output_symlink_is_rejected(tmp_path: Path) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    (attempt / "out").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(WorkerArtifactError):
        initialize_attempt_output(root, task_id=task_id, attempt=1)


def test_write_failure_leaves_no_partial_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    real_write = os.write
    count = 0

    def fail_second(descriptor: int, data: bytes) -> int:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic unavailable device")
        return real_write(descriptor, data[:2])

    monkeypatch.setattr(os, "write", fail_second)
    with pytest.raises(WorkerArtifactError):
        write_worker_artifact(
            root, task_id=task_id, attempt=1, name="result.json", data=b"complete"
        )
    assert not (attempt / "result.json").exists()
    assert list(attempt.glob(".worker-*")) == []


def test_oversized_result_is_rejected_without_file(tmp_path: Path) -> None:
    root, task_id, attempt = _prepared(tmp_path)
    with pytest.raises(WorkerArtifactError):
        write_worker_artifact(
            root, task_id=task_id, attempt=1, name="result.json", data=b"x" * (8 * 1024 * 1024 + 1)
        )
    assert not (attempt / "result.json").exists()
