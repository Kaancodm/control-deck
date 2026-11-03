"""Safe, bounded text attachment extraction at the inbox boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.privacy.files import FileInputError, read_text_input


@pytest.mark.parametrize(
    ("suffix", "mime"),
    [
        (".txt", "text/plain"),
        (".md", "text/markdown"),
        (".markdown", None),
        (".json", "application/json"),
        (".csv", "text/csv"),
        (".TXT", 'text/plain; charset="UTF-8"'),
    ],
)
def test_text_formats_preserve_complete_unicode_text(
    tmp_path: Path, suffix: str, mime: str | None
) -> None:
    inbox = tmp_path / "inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)
    content = "\ufeffGrüezi 🌱\r\nalpha,beta\n\t1,2\n"
    filename = "input" + suffix
    (nested / filename).write_bytes(content.encode("utf-8"))

    assert read_text_input(inbox, f"nested/{filename}", mime=mime) == content


@pytest.mark.parametrize(
    ("filename", "mime"),
    [
        ("input.pdf", "text/plain"),
        ("input.docx", None),
        ("input.zip", None),
        ("input.png", None),
        ("input.txt", "application/pdf"),
        ("input.json", "text/plain"),
        ("input.txt", "text/plain; charset=latin-1"),
    ],
)
def test_unsupported_format_or_mime_is_rejected(
    tmp_path: Path, filename: str, mime: str | None
) -> None:
    (tmp_path / filename).write_text("plain content", encoding="utf-8")
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, filename, mime=mime)
    assert failure.value.code == "unsupported_file_type"


@pytest.mark.parametrize("content", [b"a\x00b", b"a\x01b", b"a\x7fb", "a\u0085b".encode()])
def test_binary_control_characters_are_rejected(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "input.txt").write_bytes(content)
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt")
    assert failure.value.code == "binary_input"


def test_invalid_utf8_is_rejected_without_lossy_decoding(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_bytes(b"valid prefix\xff")
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt")
    assert failure.value.code == "invalid_utf8"


def test_size_limit_is_in_bytes_and_never_truncates(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_bytes("éé".encode())
    assert read_text_input(tmp_path, "input.txt", max_bytes=4) == "éé"
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt", max_bytes=3)
    assert failure.value.code == "file_too_large"


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_file_limit_is_rejected(tmp_path: Path, limit: int) -> None:
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt", max_bytes=limit)
    assert failure.value.code == "invalid_file_limit"


@pytest.mark.parametrize(
    "path", ["../outside.txt", "/outside.txt", "nested/../input.txt", "./input.txt", "a/./b.txt"]
)
def test_input_paths_cannot_escape_or_use_dot_segments(tmp_path: Path, path: str) -> None:
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, path)
    assert failure.value.code == "unsafe_input_path"
    assert path not in str(failure.value)


@pytest.mark.parametrize("link_kind", ["leaf", "directory", "root", "root_parent"])
def test_symlinks_are_rejected_at_every_directory_boundary(tmp_path: Path, link_kind: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside marker", encoding="utf-8")
    inbox = tmp_path / "inbox"
    filename = "private.txt"
    if link_kind == "root":
        inbox.symlink_to(outside, target_is_directory=True)
    elif link_kind == "root_parent":
        (outside / "child").mkdir()
        (outside / "child" / filename).write_text("outside marker", encoding="utf-8")
        inbox.symlink_to(outside, target_is_directory=True)
        inbox = inbox / "child"
    else:
        inbox.mkdir()
        if link_kind == "directory":
            (inbox / "nested").symlink_to(outside, target_is_directory=True)
            filename = "nested/private.txt"
        else:
            (inbox / filename).symlink_to(outside / filename)

    with pytest.raises(FileInputError) as failure:
        read_text_input(inbox, filename)
    assert failure.value.code == "unsafe_input_path"
    assert "outside marker" not in str(failure.value)
    assert str(outside) not in str(failure.value)


def test_hardlinks_are_rejected(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside marker", encoding="utf-8")
    os.link(outside, inbox / "input.txt")
    with pytest.raises(FileInputError) as failure:
        read_text_input(inbox, "input.txt")
    assert failure.value.code == "unsafe_input_path"


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_non_regular_files_are_rejected_without_reading(tmp_path: Path, kind: str) -> None:
    candidate = tmp_path / "input.txt"
    if kind == "directory":
        candidate.mkdir()
    else:
        os.mkfifo(candidate)
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt")
    assert failure.value.code == "unsupported_file_type"


def test_missing_file_error_does_not_echo_host_path(tmp_path: Path) -> None:
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "private-missing.txt")
    assert failure.value.code == "file_unavailable"
    assert str(tmp_path) not in str(failure.value)
    assert "private-missing" not in str(failure.value)


def test_leaf_replaced_with_symlink_before_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    candidate = inbox / "input.txt"
    candidate.write_text("approved marker", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside marker", encoding="utf-8")
    original_open = os.open

    def swap_before_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "input.txt":
            candidate.unlink()
            candidate.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(FileInputError) as failure:
        read_text_input(inbox, "input.txt")
    assert failure.value.code == "unsafe_input_path"


def test_directory_replacement_cannot_redirect_an_open_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = tmp_path / "inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)
    (nested / "input.txt").write_text("approved marker", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "input.txt").write_text("outside marker", encoding="utf-8")
    original_open = os.open

    def swap_after_directory_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "input.txt":
            nested.rename(inbox / "previous")
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_after_directory_open)
    assert read_text_input(inbox, "nested/input.txt") == "approved marker"


def test_opened_descriptors_are_closed_after_decoding_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "input.txt").write_bytes(b"\xff")
    original_open = os.open
    opened: list[int] = []

    def track_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", track_open)
    with pytest.raises(FileInputError):
        read_text_input(tmp_path, "input.txt")
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_file_changed_during_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "input.txt"
    candidate.write_text("initial content", encoding="utf-8")
    original_read = os.read
    changed = False

    def append_after_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if not changed:
            changed = True
            with candidate.open("ab") as stream:
                stream.write(b" appended")
        return chunk

    monkeypatch.setattr(os, "read", append_after_read)
    with pytest.raises(FileInputError) as failure:
        read_text_input(tmp_path, "input.txt")
    assert failure.value.code == "file_changed"
