"""Private immutable attempt artifacts with descriptor-relative path checks."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.core.schemas import Worker

_MAX_BYTES = 8 * 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class WorkerArtifactError(ValueError):
    """A safe failure code without paths, payload contents, or subprocess output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Worker artifact could not be stored safely.")


@dataclass(frozen=True)
class WorkerArtifact:
    relative_path: str
    sha256: str
    size_bytes: int


def _open_directory(stack: ExitStack, name: str, parent: int | None, *, private: bool) -> int:
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
    stack.callback(os.close, descriptor)
    metadata = os.fstat(descriptor)
    if private and (metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise WorkerArtifactError("unsafe_directory")
    return descriptor


def _attempt_directory(stack: ExitStack, root: Path, task_id: UUID, attempt: int) -> int:
    if (
        not isinstance(task_id, UUID)
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or not 1 <= attempt <= 2147483647
    ):
        raise WorkerArtifactError("invalid_attempt")
    root = root.absolute()
    if ".." in root.parts or not 2 <= len(root.parts) <= 64:
        raise WorkerArtifactError("unsafe_directory")
    directory = _open_directory(stack, root.anchor, None, private=False)
    for component in root.parts[1:-1]:
        directory = _open_directory(stack, component, directory, private=False)
    for component in (root.name, str(task_id), str(attempt)):
        directory = _open_directory(stack, component, directory, private=True)
    return directory


def _existing_matches(directory: int, name: str, data: bytes) -> bool:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        return False
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise WorkerArtifactError("unsafe_artifact")
        if before.st_size != len(data):
            raise WorkerArtifactError("artifact_conflict")
        offset = 0
        while chunk := os.read(descriptor, 65536):
            if data[offset : offset + len(chunk)] != chunk:
                raise WorkerArtifactError("artifact_conflict")
            offset += len(chunk)
            if offset > _MAX_BYTES:
                raise WorkerArtifactError("artifact_conflict")
        after = os.fstat(descriptor)
        if (
            offset != len(data)
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or before.st_size != after.st_size
            or after.st_nlink != 1
        ):
            raise WorkerArtifactError("artifact_changed")
        return True
    finally:
        os.close(descriptor)


def initialize_attempt_output(runs_root: Path, *, task_id: UUID, attempt: int) -> Path:
    """Create only the private output directory beneath an already prepared attempt."""
    try:
        with ExitStack() as stack:
            directory = _attempt_directory(stack, runs_root, task_id, attempt)
            try:
                os.mkdir("out", 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            _open_directory(stack, "out", directory, private=True)
            os.fsync(directory)
    except OSError:
        raise WorkerArtifactError("unsafe_directory") from None
    return runs_root.absolute() / str(task_id) / str(attempt)


def write_worker_artifact(
    runs_root: Path, *, task_id: UUID, attempt: int, name: str, data: bytes
) -> WorkerArtifact:
    """Publish a bounded result or summary log, refusing symlinks and overwrites."""
    if name not in {"result.json", *(f"{worker.value}.log" for worker in Worker)}:
        raise WorkerArtifactError("invalid_name")
    if not isinstance(data, bytes) or len(data) > _MAX_BYTES:
        raise WorkerArtifactError("artifact_too_large")
    try:
        with ExitStack() as stack:
            directory = _attempt_directory(stack, runs_root, task_id, attempt)
            if not _existing_matches(directory, name, data):
                temporary = ".worker-" + uuid4().hex + ".tmp"
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    remaining = memoryview(data)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise WorkerArtifactError("artifact_unavailable")
                        remaining = remaining[written:]
                    os.fsync(descriptor)
                    try:
                        os.link(
                            temporary,
                            name,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        _existing_matches(directory, name, data)
                finally:
                    os.close(descriptor)
                    os.unlink(temporary, dir_fd=directory)
                if not _existing_matches(directory, name, data):
                    raise WorkerArtifactError("artifact_unavailable")
                os.fsync(directory)
    except OSError:
        raise WorkerArtifactError("artifact_unavailable") from None
    return WorkerArtifact(
        f"{task_id}/{attempt}/{name}", hashlib.sha256(data).hexdigest(), len(data)
    )
