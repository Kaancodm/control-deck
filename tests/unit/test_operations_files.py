"""Maintenance filesystem boundaries reject unsafe storage instead of following it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.operations.errors import OperationError
from app.operations.files import child_directory, private_root, read_file, remove_tree, write_file


def test_private_files_roundtrip_and_tree_removal(tmp_path: Path) -> None:
    root = tmp_path / "private"
    with private_root(root, create=True) as parent:
        with child_directory(parent, "child", create=True) as directory:
            write_file(directory, "sample", b"exact bytes")
            assert read_file(directory, "sample", maximum=20) == b"exact bytes"
        assert remove_tree(parent, "child") == 1
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("location", ["root", "ancestor"])
def test_symlink_directory_is_refused(tmp_path: Path, location: str) -> None:
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    (actual / "nested").mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    target = link if location == "root" else link / "nested"
    with pytest.raises(OperationError, match="Maintenance"):
        with private_root(target):
            pytest.fail("A symlink must not be opened")


@pytest.mark.parametrize("mode", [0o755, 0o777, 0o750])
def test_nonprivate_root_is_refused(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=mode)
    root.chmod(mode)
    with pytest.raises(OperationError):
        with private_root(root):
            pytest.fail("An unsafe root must not be opened")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "fifo"])
def test_unsafe_tree_refused_before_deleting_siblings(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    outside.chmod(0o600)
    with private_root(root, create=True) as parent:
        with child_directory(parent, "child", create=True) as directory:
            write_file(directory, "safe", b"keep too")
            bad = root / "child" / "unsafe"
            if kind == "symlink":
                bad.symlink_to(outside)
            elif kind == "hardlink":
                os.link(outside, bad)
            elif kind == "fifo":
                os.mkfifo(bad, 0o600)
            else:
                bad.write_bytes(b"public")
                bad.chmod(0o644)
        with pytest.raises(OperationError):
            remove_tree(parent, "child")
    assert (root / "child" / "safe").read_bytes() == b"keep too"
    assert outside.read_bytes() == b"keep"


def test_read_and_removal_limits(tmp_path: Path) -> None:
    with private_root(tmp_path / "root", create=True) as parent:
        with child_directory(parent, "child", create=True) as directory:
            write_file(directory, "a", b"large value")
            write_file(directory, "b", b"value")
            with pytest.raises(OperationError) as error:
                read_file(directory, "a", maximum=2)
            assert error.value.code == "file_too_large"
        with pytest.raises(OperationError) as error:
            remove_tree(parent, "child", maximum_files=1)
        assert error.value.code == "tree_too_large"
    assert (tmp_path / "root" / "child" / "a").exists()
