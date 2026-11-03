"""Bounded private uploads and owner artifact reads using pinned descriptors."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from app.core.schemas import InputType, TaskInput
from app.privacy.files import read_text_input

MAX_FILE_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 5 * MAX_FILE_BYTES
_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class UiFileError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("File is unavailable or does not meet the text-input requirements.")


@dataclass(frozen=True)
class Upload:
    filename: str
    data: bytes = field(repr=False)

    def text(self) -> str:
        if (
            not self.filename
            or len(self.filename) > 200
            or "/" in self.filename
            or "\\" in self.filename
            or Path(self.filename).suffix.lower() not in _TYPES
        ):
            raise UiFileError("unsupported_file_type")
        if len(self.data) > MAX_FILE_BYTES:
            raise UiFileError("file_too_large")
        try:
            text = self.data.decode("utf-8")
        except UnicodeError:
            raise UiFileError("invalid_utf8") from None
        if any((ord(c) < 32 and c not in "\n\r\t") or 127 <= ord(c) <= 159 for c in text):
            raise UiFileError("binary_input")
        return text


def _open_dir(stack: ExitStack, name: str, parent: int | None, *, private: bool) -> int:
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
    stack.callback(os.close, descriptor)
    info = os.fstat(descriptor)
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise UiFileError("unsafe_directory")
    return descriptor


def _root(stack: ExitStack, root: Path, *, create: bool) -> int:
    root = root.absolute()
    if ".." in root.parts or not 2 <= len(root.parts) <= 64:
        raise UiFileError("unsafe_directory")
    directory = _open_dir(stack, root.anchor, None, private=False)
    for component in root.parts[1:-1]:
        directory = _open_dir(stack, component, directory, private=False)
    if create:
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=directory)
        except FileExistsError:
            pass
    return _open_dir(stack, root.name, directory, private=True)


@contextmanager
def store_uploads(
    inbox: Path, task_id: UUID, uploads: Sequence[Upload]
) -> Iterator[list[TaskInput]]:
    """Publish already inspected uploads; remove only this new batch if submission fails."""
    if len(uploads) > 16 or sum(len(item.data) for item in uploads) > MAX_UPLOAD_BYTES:
        raise UiFileError("too_many_files")
    for item in uploads:
        item.text()
    if not uploads:
        yield []
        return
    try:
        with ExitStack() as stack:
            root = _root(stack, inbox, create=True)
            name = "ui-" + task_id.hex
            # Existing directories are never reused or cleaned up.
            os.mkdir(name, mode=0o700, dir_fd=root)
            directory = _open_dir(stack, name, root, private=True)
            published: list[str] = []
            try:
                inputs = []
                for index, upload in enumerate(uploads):
                    suffix = Path(upload.filename).suffix.lower()
                    filename = f"input-{index}{suffix}"
                    fd = os.open(
                        filename,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=directory,
                    )
                    published.append(filename)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(upload.data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    path = f"{name}/{filename}"
                    if read_text_input(inbox, path, mime=_TYPES[suffix]) != upload.text():
                        raise UiFileError("file_changed")
                    inputs.append(
                        TaskInput(
                            name=upload.filename,
                            type=InputType.FILE,
                            path=path,
                            mime=_TYPES[suffix],
                        )
                    )
                yield inputs
            except BaseException:
                for filename in published:
                    os.unlink(filename, dir_fd=directory)
                os.rmdir(name, dir_fd=root)
                raise
    except OSError:
        raise UiFileError("file_unavailable") from None


def read_payload(runs: Path, task_id: UUID, attempt: int) -> str:
    """Read only the fixed payload artifact; callers supply an authenticated task identity."""
    if type(attempt) is not int or not 1 <= attempt <= 2147483647:
        raise UiFileError("invalid_attempt")
    try:
        with ExitStack() as stack:
            directory = _root(stack, runs, create=False)
            for component in (str(task_id), str(attempt)):
                directory = _open_dir(stack, component, directory, private=True)
            fd = os.open(
                "payload.sent.md",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory,
            )
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > MAX_UPLOAD_BYTES
            ):
                raise UiFileError("unsafe_artifact")
            data = bytearray()
            while chunk := os.read(fd, min(65536, MAX_UPLOAD_BYTES + 1 - len(data))):
                data.extend(chunk)
                if len(data) > MAX_UPLOAD_BYTES:
                    raise UiFileError("file_too_large")
            after = os.fstat(fd)
            if (
                len(data) != before.st_size
                or after.st_nlink != 1
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise UiFileError("file_changed")
            return data.decode("utf-8")
    except (OSError, UnicodeError):
        raise UiFileError("file_unavailable") from None
