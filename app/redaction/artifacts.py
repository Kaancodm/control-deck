"""Publish immutable, private payload artifacts without following symlinks."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

_MAX_BYTES = 5 * 1024 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_FILENAME = "payload.sent.md"


class PayloadArtifactError(ValueError):
    """Artifact failure containing a stable code, never a path or payload value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Payload artifact could not be published safely.")


@dataclass(frozen=True)
class PayloadArtifact:
    relative_path: str
    sha256: str
    size_bytes: int


def _directory(
    stack: ExitStack,
    name: str,
    *,
    parent: int | None = None,
    private: bool = False,
    create: bool = False,
) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    stack.callback(os.close, descriptor)
    metadata = os.fstat(descriptor)
    if private and (metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise PayloadArtifactError("unsafe_directory")
    return descriptor


def _matches_existing(directory: int, data: bytes) -> bool:
    """Compare exact bytes of an existing single-link private regular artifact."""
    try:
        descriptor = os.open(_FILENAME, _READ_FLAGS, dir_fd=directory)
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
            raise PayloadArtifactError("unsafe_artifact")
        if before.st_size != len(data):
            raise PayloadArtifactError("artifact_conflict")
        offset = 0
        while chunk := os.read(descriptor, 65536):
            if chunk != data[offset : offset + len(chunk)]:
                raise PayloadArtifactError("artifact_conflict")
            offset += len(chunk)
            if offset > _MAX_BYTES:
                raise PayloadArtifactError("artifact_conflict")
        after = os.fstat(descriptor)
        if (
            offset != len(data)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or after.st_nlink != 1
        ):
            raise PayloadArtifactError("artifact_changed")
        return True
    finally:
        os.close(descriptor)


def _publish(directory: int, data: bytes) -> None:
    if _matches_existing(directory, data):
        return
    temporary = ".payload-" + uuid4().hex + ".tmp"
    descriptor = os.open(temporary, _WRITE_FLAGS, 0o600, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise PayloadArtifactError("artifact_unavailable")
            remaining = remaining[written:]
        os.fsync(descriptor)
        try:
            # link() atomically publishes a complete file and refuses to replace
            # an existing name. The private temporary link is removed below.
            os.link(
                temporary,
                _FILENAME,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        except FileExistsError:
            _matches_existing(directory, data)
    finally:
        os.close(descriptor)
        os.unlink(temporary, dir_fd=directory)
    if not _matches_existing(directory, data):
        raise PayloadArtifactError("artifact_unavailable")
    os.fsync(directory)


def write_payload_sent(
    runs_root: Path, *, task_id: UUID, attempt: int, text: str
) -> PayloadArtifact:
    """Publish the exact UTF-8 bytes at ``<task>/<attempt>/payload.sent.md``.

    The configured root's parent must exist. Its ancestors are opened without
    following symlinks; the root and descendants must be private directories.
    An already published attempt is immutable. A retry with identical bytes is
    idempotent, while a conflicting concurrent writer receives a safe error.
    """
    if not isinstance(task_id, UUID):
        raise PayloadArtifactError("invalid_task_id")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 2147483647:
        raise PayloadArtifactError("invalid_attempt")
    if not isinstance(text, str) or len(text) > _MAX_BYTES:
        raise PayloadArtifactError("payload_too_large")
    try:
        data = text.encode("utf-8")
    except UnicodeError:
        raise PayloadArtifactError("invalid_utf8") from None
    if len(data) > _MAX_BYTES:
        raise PayloadArtifactError("payload_too_large")
    root = runs_root.absolute()
    if ".." in root.parts or len(root.parts) < 2 or len(root.parts) > 64:
        raise PayloadArtifactError("unsafe_directory")
    try:
        with ExitStack() as stack:
            directory = _directory(stack, root.anchor)
            for component in root.parts[1:-1]:
                directory = _directory(stack, component, parent=directory)
            directory = _directory(stack, root.name, parent=directory, private=True, create=True)
            directory = _directory(stack, str(task_id), parent=directory, private=True, create=True)
            directory = _directory(stack, str(attempt), parent=directory, private=True, create=True)
            _publish(directory, data)
    except OSError as exc:
        code = (
            "unsafe_artifact"
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}
            else "artifact_unavailable"
        )
        raise PayloadArtifactError(code) from None
    return PayloadArtifact(
        relative_path=f"{task_id}/{attempt}/{_FILENAME}",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
