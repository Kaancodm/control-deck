"""Upload and payload boundaries use real temporary filesystem objects."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.redaction.artifacts import write_payload_sent
from app.ui.files import MAX_FILE_BYTES, UiFileError, Upload, read_payload, store_uploads


@pytest.mark.parametrize(
    "name,data",
    [
        ("../x.txt", b"safe"),
        ("x.pdf", b"pdf"),
        ("x.txt", b"\xff"),
        ("x.txt", b"binary\0"),
        ("x.txt", b"a" * (MAX_FILE_BYTES + 1)),
    ],
)
def test_invalid_upload_never_creates_file(tmp_path, name, data):
    with (
        pytest.raises(UiFileError),
        store_uploads(tmp_path / "inbox", uuid4(), [Upload(name, data)]),
    ):
        pytest.fail("invalid upload was admitted")
    assert not (tmp_path / "inbox").exists()


def test_upload_preserves_bytes_and_cleans_failed_submission(tmp_path):
    inbox = tmp_path / "inbox"
    task_id = uuid4()
    with (
        pytest.raises(ValueError),
        store_uploads(inbox, task_id, [Upload("file.md", b"a\r\nb")]) as items,
    ):
        assert (inbox / items[0].path).read_bytes() == b"a\r\nb"
        raise ValueError("submission rejected")
    assert list(inbox.iterdir()) == []


def test_upload_never_replaces_existing_batch(tmp_path):
    inbox = tmp_path / "inbox"
    task_id = uuid4()
    with store_uploads(inbox, task_id, [Upload("file.md", b"original")]) as items:
        path = inbox / items[0].path
    with pytest.raises(UiFileError), store_uploads(inbox, task_id, [Upload("file.md", b"changed")]):
        pass
    assert path.read_bytes() == b"original"


def test_upload_rejects_symlink_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    inbox = tmp_path / "inbox"
    inbox.symlink_to(target)
    with pytest.raises(UiFileError), store_uploads(inbox, uuid4(), [Upload("a.txt", b"safe")]):
        pass
    assert list(target.iterdir()) == []


def test_payload_read_is_bounded_and_rejects_symlink(tmp_path):
    task_id = uuid4()
    runs = tmp_path / "runs"
    artifact = write_payload_sent(runs, task_id=task_id, attempt=1, text="safe text")
    assert read_payload(runs, task_id, 1) == "safe text"
    path = runs / artifact.relative_path
    path.unlink()
    target = tmp_path / "target.txt"
    target.write_text("hidden")
    path.symlink_to(target)
    with pytest.raises(UiFileError):
        read_payload(runs, task_id, 1)


def test_payload_read_rejects_hardlinks(tmp_path):
    task_id = uuid4()
    runs = tmp_path / "runs"
    artifact = write_payload_sent(runs, task_id=task_id, attempt=1, text="safe text")
    Path(tmp_path / "second").hardlink_to(runs / artifact.relative_path)
    with pytest.raises(UiFileError):
        read_payload(runs, task_id, 1)
