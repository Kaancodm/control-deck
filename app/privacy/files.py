"""Read bounded UTF-8 attachments without following paths outside the inbox."""

from __future__ import annotations

import errno
import os
import stat
from contextlib import ExitStack
from pathlib import Path

from app.core.paths import validate_deck_relative_path

_TEXT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
}
_ERROR_MESSAGES = {
    "unsafe_input_path": "Attachment path is not a safe inbox path.",
    "unsupported_file_type": "Attachment type is not a supported text format.",
    "invalid_utf8": "Attachment must contain valid UTF-8 text.",
    "binary_input": "Attachment contains unsupported control characters.",
    "file_too_large": "Attachment exceeds the configured byte limit.",
    "file_unavailable": "Attachment could not be read safely.",
    "file_changed": "Attachment changed while it was being read.",
    "invalid_file_limit": "Attachment byte limit must be a positive integer.",
}
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_MAX_COMPONENTS = 64
_MAX_PATH_BYTES = 4096


class FileInputError(ValueError):
    """Attachment failure containing a stable code, never raw paths or contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_ERROR_MESSAGES.get(code, "Attachment could not be read safely."))


def _validate_text_type(path: str, mime: str | None) -> None:
    expected_mime = _TEXT_TYPES.get(Path(path).suffix.lower())
    if expected_mime is None:
        raise FileInputError("unsupported_file_type")
    if mime is None:
        return
    media_type, *parameters = mime.lower().split(";")
    if media_type.strip() != expected_mime:
        raise FileInputError("unsupported_file_type")
    for parameter in parameters:
        name, separator, value = parameter.strip().partition("=")
        if (
            name.strip() != "charset"
            or not separator
            or value.strip().strip('"') not in {"utf-8", "utf8"}
        ):
            raise FileInputError("unsupported_file_type")


def _open_directory(stack: ExitStack, name: str, parent: int | None = None) -> int:
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    stack.callback(os.close, descriptor)
    return descriptor


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise FileInputError("unsupported_file_type")
    if before.st_nlink != 1:
        raise FileInputError("unsafe_input_path")
    if before.st_size > max_bytes:
        raise FileInputError("file_too_large")

    data = bytearray()
    while True:
        chunk = os.read(descriptor, min(65536, max_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise FileInputError("file_too_large")

    after = os.fstat(descriptor)
    if (
        len(data) != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_ctime_ns != before.st_ctime_ns
        or after.st_nlink != 1
    ):
        raise FileInputError("file_changed")
    return bytes(data)


def read_text_input(
    inbox: Path,
    path: str,
    *,
    mime: str | None = None,
    max_bytes: int = 1048576,
) -> str:
    """Return the complete text of one regular, single-link inbox attachment.

    Open every component relative to a pinned directory descriptor with
    ``O_NOFOLLOW``. Directory/leaf replacement cannot redirect a read through a
    symlink; FIFO opens are nonblocking and rejected before reading. Text is
    decoded strictly without stripping a BOM, normalizing, or truncating it.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise FileInputError("invalid_file_limit")
    try:
        validate_deck_relative_path(path, field="attachment")
    except ValueError:
        raise FileInputError("unsafe_input_path") from None
    components = path.split("/")
    if (
        "." in components
        or len(components) > _MAX_COMPONENTS
        or len(path.encode("utf-8")) > _MAX_PATH_BYTES
    ):
        raise FileInputError("unsafe_input_path")
    _validate_text_type(path, mime)

    root = inbox.absolute()
    if ".." in root.parts or len(root.parts) > _MAX_COMPONENTS:
        raise FileInputError("unsafe_input_path")
    try:
        with ExitStack() as stack:
            directory = _open_directory(stack, root.anchor)
            for component in (*root.parts[1:], *components[:-1]):
                directory = _open_directory(stack, component, directory)
            descriptor = os.open(components[-1], _FILE_FLAGS, dir_fd=directory)
            stack.callback(os.close, descriptor)
            data = _read_bounded(descriptor, max_bytes)
    except OSError as exc:
        code = (
            "unsafe_input_path" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "file_unavailable"
        )
        raise FileInputError(code) from None

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise FileInputError("invalid_utf8") from None
    if any((ord(char) < 32 and char not in "\t\n\r") or 127 <= ord(char) <= 159 for char in text):
        raise FileInputError("binary_input")
    return text
