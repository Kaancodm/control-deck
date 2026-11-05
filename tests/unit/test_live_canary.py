"""Canary reporting never prints model output, credentials or exception text."""

import json
from uuid import uuid4

import pytest

from app.core.schemas import DataClass, Worker
from scripts import live_canary


def test_canary_is_local_only_reviewed_single_attempt():
    task = live_canary.canary_task(uuid4())
    assert task.data_class == DataClass.LOCAL_ONLY
    assert task.policy.allowed_workers == [Worker.OLLAMA]
    assert task.fallback_chain == [Worker.OLLAMA]
    assert task.policy.max_attempts == 1
    assert task.policy.require_review


def test_canary_without_selection_is_read_only_failure(monkeypatch, capsys):
    def forbidden():
        pytest.fail("No task selection must not access the database")

    monkeypatch.setattr(live_canary, "get_session_factory", forbidden)
    assert live_canary.main([]) == 1
    assert capsys.readouterr().out == "FAIL\n"


def test_canary_exception_is_sanitized(monkeypatch, capsys):
    def unavailable():
        raise RuntimeError("synthetic-private-database-marker")

    monkeypatch.setattr(live_canary, "get_session_factory", unavailable)
    assert live_canary.main(["--task-id", str(uuid4())]) == 1
    captured = capsys.readouterr()
    assert captured.out == "FAIL\n"
    assert captured.err == ""


def test_read_log_is_bounded_and_rejects_symlinks(tmp_path):
    task_id = uuid4()
    path = tmp_path / str(task_id) / "1"
    path.mkdir(parents=True)
    log = path / "ollama.log"
    log.write_text(json.dumps({"execution_profile": "live"}))
    assert live_canary._read_log(tmp_path, task_id, 1) == log.read_bytes()
    log.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError):
        live_canary._read_log(tmp_path, task_id, 1)
    log.unlink()
    target = tmp_path / "other"
    target.write_text("private")
    log.symlink_to(target)
    with pytest.raises(OSError):
        live_canary._read_log(tmp_path, task_id, 1)
