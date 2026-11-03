"""Private, immutable artifact publication and failure cleanup."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.redaction import artifacts
from app.redaction.artifacts import PayloadArtifactError, write_payload_sent


def _target(root: Path, task_id: uuid.UUID, attempt: int = 1) -> Path:
    return root / str(task_id) / str(attempt) / "payload.sent.md"


def test_exact_unicode_bytes_hash_and_private_permissions(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    text = "\ufeffUnicode: ä Ω 🧪\r\n  trailing space \n"
    result = write_payload_sent(root, task_id=task_id, attempt=1, text=text)
    target = root / result.relative_path
    assert target.read_bytes() == text.encode("utf-8")
    assert result.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.size_bytes == len(target.read_bytes())
    for directory in (root, root / str(task_id), target.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.stat().st_nlink == 1
    assert text not in repr(result)


def test_existing_identical_artifact_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    first = write_payload_sent(root, task_id=task_id, attempt=1, text="ordinary payload")
    before = _target(root, task_id).stat()
    second = write_payload_sent(root, task_id=task_id, attempt=1, text="ordinary payload")
    after = _target(root, task_id).stat()
    assert first == second
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns


def test_empty_payload_has_the_exact_empty_digest(tmp_path: Path) -> None:
    result = write_payload_sent(tmp_path / "runs", task_id=uuid.uuid4(), attempt=1, text="")
    assert result.size_bytes == 0
    assert result.sha256 == hashlib.sha256(b"").hexdigest()


def test_short_writes_preserve_every_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    original_write = os.write
    text = "Unicode 🧪 and newlines\r\n" * 100

    def short_write(descriptor, data):
        return original_write(descriptor, data[:7])

    monkeypatch.setattr(artifacts.os, "write", short_write)
    result = write_payload_sent(root, task_id=task_id, attempt=1, text=text)
    assert _target(root, task_id).read_bytes() == text.encode()
    assert result.sha256 == hashlib.sha256(text.encode()).hexdigest()


def test_existing_different_artifact_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    write_payload_sent(root, task_id=task_id, attempt=1, text="original payload")
    with pytest.raises(PayloadArtifactError) as caught:
        write_payload_sent(root, task_id=task_id, attempt=1, text="replacement payload")
    assert caught.value.code == "artifact_conflict"
    assert _target(root, task_id).read_text() == "original payload"


@pytest.mark.parametrize("position", ["ancestor", "root", "task", "attempt", "leaf"])
def test_symlinks_are_rejected_without_touching_outside(tmp_path: Path, position: str) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    marker = outside / "marker.txt"
    marker.write_text("untouched")
    if position == "ancestor":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        root = alias / "runs"
    elif position == "root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        task_directory = root / str(task_id)
        if position == "task":
            task_directory.symlink_to(outside, target_is_directory=True)
        else:
            task_directory.mkdir(mode=0o700)
            attempt_directory = task_directory / "1"
            if position == "attempt":
                attempt_directory.symlink_to(outside, target_is_directory=True)
            else:
                attempt_directory.mkdir(mode=0o700)
                _target(root, task_id).symlink_to(marker)
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(root, task_id=task_id, attempt=1, text="replacement")
    assert marker.read_text() == "untouched"
    assert list(outside.iterdir()) == [marker]


@pytest.mark.parametrize("kind", ["hardlink", "fifo", "directory", "public_file"])
def test_unsafe_existing_artifacts_are_rejected(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    target = _target(root, task_id)
    target.parent.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    (root / str(task_id)).chmod(0o700)
    if kind == "hardlink":
        outside = tmp_path / "outside.txt"
        outside.write_text("ordinary payload")
        outside.chmod(0o600)
        target.hardlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(target, mode=0o600)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    else:
        target.write_text("ordinary payload")
        target.chmod(0o644)
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(root, task_id=task_id, attempt=1, text="ordinary payload")


@pytest.mark.parametrize("position", ["root", "task", "attempt"])
def test_existing_public_directory_is_rejected_without_chmod(tmp_path: Path, position: str) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    directories = [root, root / str(task_id), root / str(task_id) / "1"]
    for directory in directories:
        directory.mkdir(mode=0o700)
    bad = directories[["root", "task", "attempt"].index(position)]
    bad.chmod(0o755)
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(root, task_id=task_id, attempt=1, text="ordinary payload")
    assert stat.S_IMODE(bad.stat().st_mode) == 0o755


@pytest.mark.parametrize("attempt", [0, -1, True, "../escape", 2147483648])
def test_invalid_attempt_has_no_filesystem_effect(tmp_path: Path, attempt: object) -> None:
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(tmp_path / "runs", task_id=uuid.uuid4(), attempt=attempt, text="x")
    assert list(tmp_path.iterdir()) == []


def test_invalid_task_id_has_no_filesystem_effect(tmp_path: Path) -> None:
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(tmp_path / "runs", task_id="../escape", attempt=1, text="x")
    assert list(tmp_path.iterdir()) == []


def test_parent_traversal_in_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(
            tmp_path / "unused" / ".." / "runs", task_id=uuid.uuid4(), attempt=1, text="x"
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("text", ["x" * (5 * 1024 * 1024 + 1), "é" * (3 * 1024 * 1024), "\ud800"])
def test_oversized_or_invalid_utf8_is_rejected_without_files(tmp_path: Path, text: str) -> None:
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(tmp_path / "runs", task_id=uuid.uuid4(), attempt=1, text=text)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["write", "fsync", "link"])
def test_failure_before_publication_removes_own_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()

    def fail(*args, **kwargs):
        raise OSError("private synthetic payload path")

    monkeypatch.setattr(artifacts.os, operation, fail)
    with pytest.raises(PayloadArtifactError) as caught:
        write_payload_sent(root, task_id=task_id, attempt=1, text="private synthetic payload")
    assert "private" not in str(caught.value)
    assert str(root) not in str(caught.value)
    assert list(_target(root, task_id).parent.iterdir()) == []


def test_failure_after_partial_write_never_publishes_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    original_write = os.write
    calls = 0

    def partial_write_then_fail(descriptor, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, data[:4])
        return 0

    monkeypatch.setattr(artifacts.os, "write", partial_write_then_fail)
    with pytest.raises(PayloadArtifactError):
        write_payload_sent(root, task_id=task_id, attempt=1, text="ordinary payload")
    assert list(_target(root, task_id).parent.iterdir()) == []


def test_concurrent_conflicting_writers_publish_one_complete_payload(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    texts = ["first payload\n" * 1000, "second payload\n" * 1000]

    def publish(text: str):
        try:
            return write_payload_sent(root, task_id=task_id, attempt=1, text=text)
        except PayloadArtifactError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, texts))
    data = _target(root, task_id).read_bytes()
    assert data in [text.encode() for text in texts]
    assert any(result is not None for result in results)
    assert all(
        result is None or result.sha256 == hashlib.sha256(data).hexdigest() for result in results
    )
    assert [item.name for item in _target(root, task_id).parent.iterdir()] == ["payload.sent.md"]


def test_concurrent_identical_writers_never_return_a_different_hash(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    task_id = uuid.uuid4()
    data = "ordinary payload\n" * 1000

    def publish(_):
        try:
            return write_payload_sent(root, task_id=task_id, attempt=1, text=data)
        except PayloadArtifactError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    assert any(result is not None for result in results)
    assert all(
        result is None or result.sha256 == hashlib.sha256(data.encode()).hexdigest()
        for result in results
    )
    assert _target(root, task_id).read_text() == data
