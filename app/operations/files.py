"""Descriptor-relative maintenance paths and bounded private files."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.operations.errors import OperationError

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def validate_private(metadata: os.stat_result, *, directory: bool) -> None:
    expected = 0o700 if directory else 0o600
    correct_kind = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (
        not correct_kind
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != expected
        or (not directory and metadata.st_nlink != 1)
    ):
        raise OperationError("unsafe_path")


@contextmanager
def private_root(path: Path, *, create: bool = False) -> Iterator[int]:
    """Reject symlinks in all ancestors and require an owner-only final directory."""
    absolute = path.absolute()
    if ".." in absolute.parts or not 2 <= len(absolute.parts) <= 64:
        raise OperationError("unsafe_path")
    descriptor = os.open(absolute.anchor, DIRECTORY_FLAGS)
    try:
        for index, part in enumerate(absolute.parts[1:]):
            last = index == len(absolute.parts) - 2
            if last and create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            following = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        validate_private(os.fstat(descriptor), directory=True)
        yield descriptor
    except OSError:
        raise OperationError("unsafe_path") from None
    finally:
        os.close(descriptor)


@contextmanager
def child_directory(parent: int, name: str, *, create: bool = False) -> Iterator[int]:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise OperationError("unsafe_path")
    try:
        if create:
            os.mkdir(name, 0o700, dir_fd=parent)
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        try:
            validate_private(os.fstat(descriptor), directory=True)
            yield descriptor
        finally:
            os.close(descriptor)
    except OSError:
        raise OperationError("unsafe_path") from None


def write_file(directory: int, name: str, data: bytes) -> None:
    descriptor = os.open(name, WRITE_FLAGS, 0o600, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(data)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_file(directory: int, name: str, *, maximum: int) -> bytes:
    descriptor = os.open(name, READ_FLAGS, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        validate_private(before, directory=False)
        if before.st_size > maximum:
            raise OperationError("file_too_large")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        if len(data) > maximum or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_nlink,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
            raise OperationError("file_changed")
        return data
    finally:
        os.close(descriptor)


def digest_file(descriptor: int, *, maximum: int) -> tuple[str, int]:
    before = os.fstat(descriptor)
    validate_private(before, directory=False)
    if before.st_size > maximum:
        raise OperationError("file_too_large")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    count = 0
    while chunk := os.read(descriptor, 65536):
        count += len(chunk)
        if count > maximum:
            raise OperationError("file_too_large")
        digest.update(chunk)
    after = os.fstat(descriptor)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_nlink,
    ):
        raise OperationError("file_changed")
    return digest.hexdigest(), count


def remove_tree(parent: int, name: str, *, maximum_files: int = 10000) -> int:
    """Remove a bounded private tree without following links, including during races."""
    files = 0

    def walk(directory: int, *, depth: int, remove: bool) -> None:
        nonlocal files
        if depth > 8:
            raise OperationError("tree_too_deep")
        for entry in os.listdir(directory):
            info = os.stat(entry, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                with child_directory(directory, entry) as child:
                    walk(child, depth=depth + 1, remove=remove)
                    if (
                        os.stat(entry, dir_fd=directory, follow_symlinks=False).st_ino
                        != info.st_ino
                    ):
                        raise OperationError("file_changed")
                if remove:
                    os.rmdir(entry, dir_fd=directory)
            else:
                validate_private(info, directory=False)
                files += 1
                if files > maximum_files:
                    raise OperationError("tree_too_large")
                if remove:
                    os.unlink(entry, dir_fd=directory)

    with child_directory(parent, name) as root:
        inode = os.fstat(root).st_ino
        walk(root, depth=0, remove=False)
        files = 0
        walk(root, depth=0, remove=True)
        if os.stat(name, dir_fd=parent, follow_symlinks=False).st_ino != inode:
            raise OperationError("file_changed")
    os.rmdir(name, dir_fd=parent)
    os.fsync(parent)
    return files
